from __future__ import annotations

from app.core.clock import institute_today

import uuid
from datetime import date

from fastapi import APIRouter, Depends

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.core.exceptions import AuthorizationError
from app.employees.repository import EmployeeRepository
from app.library.issue_service import MAX_RENEWALS, IssueService
from app.library.models import BorrowerType
from app.library.repository import BookCopyRepository, BookIssueRepository, BookRepository
from app.library.request_router import _to_detail
from app.library.request_schemas import (
    BorrowRequestCreate,
    BorrowRequestDetail,
    BorrowRequestOut,
    MyLoanOut,
    MyLoansResponse,
)
from app.library.request_service import BookBorrowRequestService
from app.students.repository import StudentRepository

router = APIRouter(prefix="/library/me", tags=["library"])


async def _resolve_own_borrower(db, current_user: CurrentUser) -> tuple[BorrowerType, uuid.UUID] | None:
    """Maps a logged-in user to their own Student or Employee record —
    same pattern as app/fees/assignment_router.py::_resolve_own_payer.
    Never trusts a client-supplied borrower id; a student/teacher can
    only ever act as themselves through these endpoints."""
    student = await StudentRepository(db).get_by_user_id(current_user.id)
    if student is not None:
        return BorrowerType.STUDENT, student.id
    employee = await EmployeeRepository(db).get_by_user_id(current_user.id)
    if employee is not None:
        return BorrowerType.EMPLOYEE, employee.id
    return None


async def _require_own_borrower(db, current_user: CurrentUser) -> tuple[BorrowerType, uuid.UUID]:
    own = await _resolve_own_borrower(db, current_user)
    if own is None:
        raise AuthorizationError("This account is not linked to a student or employee record.")
    return own


async def _to_loan_out(db, issue) -> MyLoanOut:
    copy = await BookCopyRepository(db).get_by_id(issue.book_copy_id)
    book = await BookRepository(db).get_by_id(copy.book_id) if copy else None
    is_overdue = issue.status.value == "ISSUED" and issue.due_date < institute_today()
    can_renew = issue.status.value == "ISSUED" and not is_overdue and issue.renewal_count < MAX_RENEWALS
    return MyLoanOut(
        issue_id=issue.id,
        book_id=book.id if book else uuid.UUID(int=0),
        book_title=book.title if book else "Unknown title",
        is_journal=book.is_journal if book else False,
        accession_code=copy.accession_code if copy else "\u2014",
        issued_at=issue.issued_at,
        due_date=issue.due_date,
        returned_at=issue.returned_at,
        is_overdue=is_overdue,
        status=issue.status.value,
        renewal_count=issue.renewal_count,
        can_renew=can_renew,
    )


@router.get("/loans", response_model=MyLoansResponse, summary="Books/journals currently or previously allotted to me")
async def my_loans(
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_VIEW)),
    db=Depends(get_db),
) -> MyLoansResponse:
    borrower_type, borrower_id = await _require_own_borrower(db, current_user)
    issue_repo = BookIssueRepository(db)
    active = await issue_repo.list_active_for_borrower(borrower_type, borrower_id)
    history = await issue_repo.list_history_for_borrower(borrower_type, borrower_id)
    return MyLoansResponse(
        active=[await _to_loan_out(db, i) for i in active],
        history=[await _to_loan_out(db, i) for i in history],
    )


@router.get("/requests", response_model=list[BorrowRequestDetail], summary="My borrow requests and their status")
async def my_requests(
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_REQUEST)),
    db=Depends(get_db),
) -> list[BorrowRequestDetail]:
    borrower_type, borrower_id = await _require_own_borrower(db, current_user)
    requests = await BookBorrowRequestService(db).list_own(borrower_type, borrower_id)
    return [await _to_detail(db, r) for r in requests]


@router.post(
    "/requests", response_model=BorrowRequestOut, status_code=201, summary="Ask the librarian to issue me a title"
)
async def submit_request(
    payload: BorrowRequestCreate,
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_REQUEST)),
    db=Depends(get_db),
) -> BorrowRequestOut:
    borrower_type, borrower_id = await _require_own_borrower(db, current_user)
    request = await BookBorrowRequestService(db).submit(
        book_id=payload.book_id,
        borrower_type=borrower_type,
        borrower_id=borrower_id,
        requested_by=current_user.id,
        note=payload.note,
    )
    return BorrowRequestOut.model_validate(request)


@router.post(
    "/requests/{request_id}/cancel", response_model=BorrowRequestOut, summary="Withdraw one of my own pending requests"
)
async def cancel_request(
    request_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_REQUEST)),
    db=Depends(get_db),
) -> BorrowRequestOut:
    borrower_type, borrower_id = await _require_own_borrower(db, current_user)
    request = await BookBorrowRequestService(db).cancel_own(
        request_id, borrower_type=borrower_type, borrower_id=borrower_id
    )
    return BorrowRequestOut.model_validate(request)


@router.post(
    "/loans/{issue_id}/renew",
    response_model=MyLoanOut,
    summary=f"Renew one of my own active loans (max {MAX_RENEWALS} times, not while overdue)",
)
async def renew_loan(
    issue_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_VIEW)),
    db=Depends(get_db),
) -> MyLoanOut:
    borrower_type, borrower_id = await _require_own_borrower(db, current_user)
    issue = await BookIssueRepository(db).get_by_id(issue_id)
    if issue is None or issue.borrower_type != borrower_type or issue.borrower_id != borrower_id:
        # Same "not found" a stranger's ID would produce.
        from app.core.exceptions import ResourceNotFoundError

        raise ResourceNotFoundError("Loan not found.")

    issue = await IssueService(db).renew(issue_id, actor_id=current_user.id)
    return await _to_loan_out(db, issue)