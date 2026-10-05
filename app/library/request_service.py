from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError
from app.library.issue_service import IssueService
from app.library.models import BookBorrowRequest, BorrowerType, BorrowRequestStatus
from app.library.repository import BookRepository
from app.library.request_repository import BookBorrowRequestRepository
from app.library.schemas import IssueBookRequest


class BookBorrowRequestService:
    """Student/faculty self-service: 'please issue me this title.' The
    librarian (or admin) still makes the actual issue decision — approving
    a request just automates picking an available copy and calling the
    same IssueService.issue_book the front-desk flow uses, so a request
    that gets approved is indistinguishable in book_issues from one a
    librarian created directly."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = BookBorrowRequestRepository(session)
        self.book_repo = BookRepository(session)
        self.issue_service = IssueService(session)
        self.audit = AuditService(session)

    async def get(self, request_id: uuid.UUID) -> BookBorrowRequest:
        request = await self.repo.get_by_id(request_id)
        if request is None:
            raise ResourceNotFoundError("Borrow request not found.")
        return request

    async def submit(
        self,
        *,
        book_id: uuid.UUID,
        borrower_type: BorrowerType,
        borrower_id: uuid.UUID,
        requested_by: uuid.UUID,
        note: str | None,
    ) -> BookBorrowRequest:
        book = await self.book_repo.get_by_id(book_id)
        if book is None:
            raise ResourceNotFoundError("Book not found.")

        check = await self.issue_service.borrower_search.eligibility(borrower_type, borrower_id)
        if not check["can_borrow"]:
            raise BusinessRuleViolationError(check["reason"])

        existing_pending = await self.repo.count_pending_for_borrower_and_book(borrower_type, borrower_id, book_id)
        if existing_pending > 0:
            raise BusinessRuleViolationError("You already have a pending request for this title.")

        request = BookBorrowRequest(
            book_id=book_id,
            borrower_type=borrower_type,
            borrower_id=borrower_id,
            requested_by=requested_by,
            note=note,
            status=BorrowRequestStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        await self.repo.create(request)
        await self.audit.log(
            actor_user_id=requested_by,
            action="library.borrow_request_submitted",
            entity_type="book_borrow_request",
            entity_id=request.id,
            after={"book_id": str(book_id), "book_title": book.title},
        )
        return request

    async def list_own(self, borrower_type: BorrowerType, borrower_id: uuid.UUID) -> list[BookBorrowRequest]:
        return await self.repo.list_for_borrower(borrower_type, borrower_id)

    async def list_all(self, status: BorrowRequestStatus | None) -> list[BookBorrowRequest]:
        return await self.repo.list_by_status(status)

    async def cancel_own(
        self, request_id: uuid.UUID, *, borrower_type: BorrowerType, borrower_id: uuid.UUID
    ) -> BookBorrowRequest:
        request = await self.get(request_id)
        if request.borrower_type != borrower_type or request.borrower_id != borrower_id:
            # Same "not found" a stranger's ID would produce — doesn't
            # confirm to a prober that the ID is valid but someone else's.
            raise ResourceNotFoundError("Borrow request not found.")
        if request.status not in (BorrowRequestStatus.PENDING, BorrowRequestStatus.WAITLISTED):
            raise BusinessRuleViolationError(f"This request is already {request.status.value.lower()}.")
        request.status = BorrowRequestStatus.CANCELLED
        return request

    async def approve(
        self, request_id: uuid.UUID, *, actor_id: uuid.UUID, due_date=None, decision_note: str | None = None
    ) -> BookBorrowRequest:
        request = await self.get(request_id)
        if request.status != BorrowRequestStatus.PENDING:
            raise BusinessRuleViolationError(f"This request is already {request.status.value.lower()}.")

        copy = await self.issue_service.copy_repo.get_first_available_for_book(request.book_id)
        if copy is None:
            # No hard failure — every copy is out, so this goes on the
            # waitlist instead and gets auto-fulfilled FIFO the next
            # time a copy of this book is returned (see
            # IssueService._auto_fulfill_waitlist). The librarian's
            # "approve" click is honored in spirit; there's just nothing
            # to hand over yet.
            request.status = BorrowRequestStatus.WAITLISTED
            request.decided_by = actor_id
            request.decided_at = datetime.now(timezone.utc)
            request.decision_note = decision_note or "No copy available — added to the waitlist."

            await self.audit.log(
                actor_user_id=actor_id,
                action="library.borrow_request_waitlisted",
                entity_type="book_borrow_request",
                entity_id=request.id,
            )
            return request

        issue = await self.issue_service.issue_book(
            IssueBookRequest(
                book_copy_id=copy.id,
                borrower_type=request.borrower_type,
                borrower_id=request.borrower_id,
                due_date=due_date,
            ),
            actor_id=actor_id,
        )

        request.status = BorrowRequestStatus.APPROVED
        request.decided_by = actor_id
        request.decided_at = datetime.now(timezone.utc)
        request.decision_note = decision_note
        request.resulting_issue_id = issue.id

        await self.audit.log(
            actor_user_id=actor_id,
            action="library.borrow_request_approved",
            entity_type="book_borrow_request",
            entity_id=request.id,
            after={"resulting_issue_id": str(issue.id), "book_copy_id": str(copy.id)},
        )
        return request

    async def reject(
        self, request_id: uuid.UUID, *, actor_id: uuid.UUID, decision_note: str | None = None
    ) -> BookBorrowRequest:
        request = await self.get(request_id)
        if request.status != BorrowRequestStatus.PENDING:
            raise BusinessRuleViolationError(f"This request is already {request.status.value.lower()}.")

        request.status = BorrowRequestStatus.REJECTED
        request.decided_by = actor_id
        request.decided_at = datetime.now(timezone.utc)
        request.decision_note = decision_note

        await self.audit.log(
            actor_user_id=actor_id,
            action="library.borrow_request_rejected",
            entity_type="book_borrow_request",
            entity_id=request.id,
            metadata={"decision_note": decision_note} if decision_note else None,
        )
        return request