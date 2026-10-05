from __future__ import annotations

from app.core.clock import institute_today
from app.core.config import settings

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
from app.core.search import name_search_condition
from app.employees.models import Employee, EmployeeStatus
from app.library.models import BookCopy, BookCopyStatus, BookIssue, BorrowerType, BorrowRequestStatus, IssueStatus
from app.library.repository import BookCopyRepository, BookIssueRepository, BookRepository
from app.library.request_repository import BookBorrowRequestRepository
from app.library.schemas import BookIssueDetail, BorrowerSearchResult, IssueBookRequest, ReturnBookRequest
from app.notifications.models import NotificationCategory
from app.notifications.service import create_notification
from app.programs.models import Program
from app.students.models import Student, StudentStatus

DEFAULT_LOAN_PERIOD_DAYS = 14
MAX_RENEWALS = 2  # ASSUMPTION — a common library default; adjust if a different policy is wanted.


@dataclass
class BorrowerDetails:
    """Everything a librarian needs to actually identify who's asking —
    not just a name, which is ambiguous with common names across a
    student body. For a student that's their program + current
    semester ('which class'); for an employee, their department, since
    'semester' doesn't apply to staff."""

    display_name: str
    identifier: str | None = None  # roll number or employee code
    program_name: str | None = None
    semester: int | None = None
    department: str | None = None


class BorrowerSearchService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search(self, query: str, *, limit: int = 10) -> list[BorrowerSearchResult]:
        if not query or len(query.strip()) < 2:
            return []

        student_stmt = (
            select(Student)
            .where(
                or_(
                    name_search_condition(Student.first_name, Student.last_name, query),
                    Student.roll_number == query,
                )
            )
            .limit(limit)
        )
        students = (await self.session.execute(student_stmt)).scalars().all()

        employee_stmt = (
            select(Employee)
            .where(name_search_condition(Employee.first_name, Employee.last_name, query))
            .limit(limit)
        )
        employees = (await self.session.execute(employee_stmt)).scalars().all()

        results = []
        for s in students:
            check = await self.eligibility(BorrowerType.STUDENT, s.id)
            results.append(BorrowerSearchResult(
                borrower_type=BorrowerType.STUDENT, borrower_id=s.id, display_name=f"{s.first_name} {s.last_name}",
                identifier=s.roll_number or "\u2014", can_borrow=check["can_borrow"], blocked_reason=check["reason"],
                active_loans=check["active_loans"], loan_limit=check["loan_limit"],
            ))
        for e in employees:
            check = await self.eligibility(BorrowerType.EMPLOYEE, e.id)
            results.append(BorrowerSearchResult(
                borrower_type=BorrowerType.EMPLOYEE, borrower_id=e.id, display_name=f"{e.first_name} {e.last_name}",
                identifier=e.employee_code or str(e.id)[:8], can_borrow=check["can_borrow"], blocked_reason=check["reason"],
                active_loans=check["active_loans"], loan_limit=check["loan_limit"],
            ))
        return results[:limit]

    async def resolve_name(self, borrower_type: BorrowerType, borrower_id: uuid.UUID) -> str:
        if borrower_type == BorrowerType.STUDENT:
            student = await self.session.get(Student, borrower_id)
            return f"{student.first_name} {student.last_name}" if student else "Unknown student"
        employee = await self.session.get(Employee, borrower_id)
        return f"{employee.first_name} {employee.last_name}" if employee else "Unknown employee"

    async def resolve_details(self, borrower_type: BorrowerType, borrower_id: uuid.UUID) -> BorrowerDetails:
        """Used wherever a librarian needs to identify a borrower at a
        glance — e.g. the Borrow Requests queue, where several students
        can share a name and 'which class/semester are they in' is the
        actual disambiguator a librarian asked for."""
        if borrower_type == BorrowerType.STUDENT:
            student = await self.session.get(Student, borrower_id)
            if student is None:
                return BorrowerDetails(display_name="Unknown student")
            program_name = None
            if student.program_id is not None:
                program = await self.session.get(Program, student.program_id)
                program_name = program.name if program else None
            return BorrowerDetails(
                display_name=f"{student.first_name} {student.last_name}",
                identifier=student.roll_number,
                program_name=program_name,
                semester=student.current_semester,
            )

        employee = await self.session.get(Employee, borrower_id)
        if employee is None:
            return BorrowerDetails(display_name="Unknown employee")
        return BorrowerDetails(
            display_name=f"{employee.first_name} {employee.last_name}",
            identifier=employee.employee_code,
            department=employee.department,
        )

    async def eligibility(self, borrower_type: BorrowerType, borrower_id: uuid.UUID) -> dict:
        """Can this person borrow a book RIGHT NOW? Returns
        {can_borrow, reason, active_loans, loan_limit}. The order is the order a librarian would
        explain it: are they even a current member -> overdue books -> unpaid fines -> too many books."""
        from app.fines.models import FinePayerType
        from app.fines.repository import FineRepository

        is_student = borrower_type == BorrowerType.STUDENT
        limit = settings.library_max_loans_student if is_student else settings.library_max_loans_employee
        person = await self.session.get(Student if is_student else Employee, borrower_id)
        if person is None:
            return {"can_borrow": False, "reason": "Borrower not found.", "active_loans": 0, "loan_limit": limit}

        loans = await BookIssueRepository(self.session).list_active_for_borrower(borrower_type, borrower_id)
        result = {"can_borrow": True, "reason": None, "active_loans": len(loans), "loan_limit": limit}

        def block(reason: str) -> dict:
            result.update(can_borrow=False, reason=reason)
            return result

        if is_student and person.status != StudentStatus.ACTIVE:
            return block(f"This student is {person.status.value.title()} and cannot borrow books.")
        if not is_student and person.status == EmployeeStatus.TERMINATED:
            return block("This employee has left the institute and cannot borrow books.")

        if settings.library_block_if_overdue:
            overdue = [loan for loan in loans if loan.status == IssueStatus.ISSUED and loan.due_date < institute_today()]
            if overdue:
                return block(f"{len(overdue)} overdue book{'s' if len(overdue) > 1 else ''} must be returned first.")

        if settings.library_block_if_unpaid_fines:
            balance = await FineRepository(self.session).balance_for(FinePayerType(borrower_type.value), borrower_id)
            if balance["total_pending"] > 0:
                return block(f"Unpaid fines of Rs. {balance['total_pending']:,.0f} must be cleared first.")

        if len(loans) >= limit:
            return block(f"Borrowing limit reached ({limit} book{'s' if limit != 1 else ''}). A book must be returned first.")
        return result

    async def borrower_exists(self, borrower_type: BorrowerType, borrower_id: uuid.UUID) -> bool:
        if borrower_type == BorrowerType.STUDENT:
            return (await self.session.get(Student, borrower_id)) is not None
        return (await self.session.get(Employee, borrower_id)) is not None


class IssueService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.copy_repo = BookCopyRepository(session)
        self.issue_repo = BookIssueRepository(session)
        self.book_repo = BookRepository(session)
        self.borrower_search = BorrowerSearchService(session)
        self.audit = AuditService(session)

    async def issue_book(self, data: IssueBookRequest, *, actor_id: uuid.UUID) -> BookIssue:
        copy = await self.copy_repo.get_by_id(data.book_copy_id)
        if copy is None:
            raise ResourceNotFoundError("Book copy not found.")
        if copy.status != BookCopyStatus.AVAILABLE:
            raise BusinessRuleViolationError(f"This copy is not available (current status: {copy.status.value}).")

        if not await self.borrower_search.borrower_exists(data.borrower_type, data.borrower_id):
            raise ResourceNotFoundError("Borrower not found.")

        eligibility = await self.borrower_search.eligibility(data.borrower_type, data.borrower_id)
        if not eligibility["can_borrow"]:
            raise BusinessRuleViolationError(eligibility["reason"])

        # NOTE: the old "library subscription" good-standing gate that used
        # to sit here has been removed — the library_subscriptions table it
        # queried was dropped by migration 65bb0065e185 when that module
        # was replaced by the general Fee module, but this call site was
        # never updated, so every issue attempt was crashing with a
        # "relation does not exist" error. Per current approved design,
        # borrowing is open to any active student/employee — no fee-dues
        # gate for now (fee-based gating can be reintroduced later against
        # the real Fee module if the business wants it).

        today = institute_today()
        due = data.due_date or (today + timedelta(days=DEFAULT_LOAN_PERIOD_DAYS))
        if due <= today:
            raise BusinessRuleViolationError("Due date must be in the future.")

        issue = BookIssue(
            book_copy_id=copy.id,
            borrower_type=data.borrower_type,
            borrower_id=data.borrower_id,
            issued_by=actor_id,
            issued_at=today,
            due_date=due,
            status=IssueStatus.ISSUED,
            created_at=datetime.now(timezone.utc),
        )
        await self.issue_repo.create(issue)
        copy.status = BookCopyStatus.ISSUED

        await self.audit.log(
            actor_user_id=actor_id,
            action="library.book_issued",
            entity_type="book_issue",
            entity_id=issue.id,
            after={
                "book_copy_id": str(copy.id),
                "borrower_type": data.borrower_type.value,
                "borrower_id": str(data.borrower_id),
                "due_date": str(due),
            },
        )
        return issue

    @staticmethod
    def late_fee(due_date: date, returned_on: date) -> tuple[int, float]:
        """(days late, standard late fee in Rs.). Rate and cap come from the library settings."""
        days_late = max((returned_on - due_date).days, 0)
        fee = days_late * settings.library_late_fee_per_day
        if settings.library_late_fee_max > 0:
            fee = min(fee, settings.library_late_fee_max)
        return days_late, round(fee, 2)

    async def return_preview(self, issue_id: uuid.UUID) -> dict:
        """What returning this book today would cost (shown to the librarian before they confirm)."""
        issue = await self.issue_repo.get_by_id(issue_id)
        if issue is None:
            raise ResourceNotFoundError("Issue record not found.")
        if issue.status != IssueStatus.ISSUED:
            raise BusinessRuleViolationError(f"This issue is already {issue.status.value.lower()}.")
        copy = await self.copy_repo.get_by_id(issue.book_copy_id)
        book = await self.book_repo.get_by_id(copy.book_id) if copy else None
        days_late, fee = self.late_fee(issue.due_date, institute_today())
        return {
            "issue_id": issue.id, "book_title": book.title if book else "Unknown book",
            "borrower_name": await self.borrower_search.resolve_name(issue.borrower_type, issue.borrower_id),
            "due_date": issue.due_date, "days_overdue": days_late,
            "late_fee_per_day": settings.library_late_fee_per_day, "suggested_fine": fee,
        }

    async def return_book(
        self, issue_id: uuid.UUID, data: ReturnBookRequest, *, actor_id: uuid.UUID, actor_roles: list[str]
    ) -> BookIssue:
        issue = await self.issue_repo.get_by_id(issue_id)
        if issue is None:
            raise ResourceNotFoundError("Issue record not found.")
        if issue.status != IssueStatus.ISSUED:
            raise BusinessRuleViolationError(f"This issue is already {issue.status.value.lower()}.")

        copy = await self.copy_repo.get_by_id(issue.book_copy_id)

        issue.returned_at = institute_today()
        issue.returned_to = actor_id
        issue.return_condition_notes = data.condition_notes
        # fine_amount: None = charge the standard late fee (if the book is late); a number = the
        # librarian's own decision (0 = waive it).
        fine_amount = data.fine_amount
        if fine_amount is None and not data.is_lost:
            fine_amount = self.late_fee(issue.due_date, issue.returned_at)[1] or None
        data = data.model_copy(update={"fine_amount": fine_amount})
        issue.fine_amount = data.fine_amount
        issue.status = IssueStatus.LOST if data.is_lost else IssueStatus.RETURNED

        if copy is not None:
            if data.is_lost:
                copy.status = BookCopyStatus.LOST
            elif data.condition_notes and "damag" in data.condition_notes.lower():
                copy.status = BookCopyStatus.DAMAGED
            else:
                copy.status = BookCopyStatus.AVAILABLE

        await self.audit.log(
            actor_user_id=actor_id,
            action="library.book_returned" if not data.is_lost else "library.book_reported_lost",
            entity_type="book_issue",
            entity_id=issue.id,
            after={"status": issue.status.value, "fine_amount": data.fine_amount},
        )

        # Cross-module connection: a real Fine record, not just a local
        # float on this row — this is what makes the fine actually
        # visible to and collectible through the Fines module (payment
        # recording, waiving, reporting). See FineSource.LIBRARY and
        # Fine.related_entity_id in app/fines/models.py, which existed
        # specifically for this and were never actually called from here.
        if data.fine_amount is not None and data.fine_amount > 0:
            from app.fines.models import FinePayerType, FineSource
            from app.fines.service import FineService

            book_title = None
            if copy is not None:
                book = await self.book_repo.get_by_id(copy.book_id)
                book_title = book.title if book else None
            reason = (
                f"Lost book{f' — {book_title}' if book_title else ''}"
                if data.is_lost
                else f"Late book return{f' — {book_title}' if book_title else ''}"
            )
            await FineService(self.session).request(
                payer_type=FinePayerType(issue.borrower_type.value),
                payer_id=issue.borrower_id,
                amount=data.fine_amount,
                reason=reason,
                source=FineSource.LIBRARY,
                actor_id=actor_id,
                actor_roles=actor_roles,
                related_entity_type="book_issue",
                related_entity_id=issue.id,
            )

        if copy is not None and copy.status == BookCopyStatus.AVAILABLE:
            await self._auto_fulfill_waitlist(copy, actor_id=actor_id)

        return issue

    async def _auto_fulfill_waitlist(self, copy: BookCopy, *, actor_id: uuid.UUID) -> None:
        """Called right after a copy becomes AVAILABLE again. If anyone is
        waitlisted for this title, issues this exact copy to whoever has
        been waiting longest (FIFO) and marks their request APPROVED —
        the same end state as if a librarian had manually approved it,
        just automatic. Does nothing if nobody is waitlisted."""

        request_repo = BookBorrowRequestRepository(self.session)
        waitlisted = await request_repo.list_by_status(BorrowRequestStatus.WAITLISTED)
        candidates = sorted((r for r in waitlisted if r.book_id == copy.book_id), key=lambda r: r.created_at)
        if not candidates:
            return

        # First person in the queue who is allowed to borrow right now. Someone who is blocked at this
        # moment (owes a fine, has an overdue book...) keeps their place in the queue and is skipped.
        next_request = None
        for candidate in candidates:
            if (await self.borrower_search.eligibility(candidate.borrower_type, candidate.borrower_id))["can_borrow"]:
                next_request = candidate
                break
        if next_request is None:
            return
        issue = await self.issue_book(
            IssueBookRequest(
                book_copy_id=copy.id, borrower_type=next_request.borrower_type, borrower_id=next_request.borrower_id
            ),
            actor_id=actor_id,
        )
        next_request.status = BorrowRequestStatus.APPROVED
        next_request.resulting_issue_id = issue.id
        next_request.decided_by = actor_id
        next_request.decided_at = datetime.now(timezone.utc)
        next_request.decision_note = "Auto-fulfilled from the waitlist when a copy was returned."

        await self.audit.log(
            actor_user_id=actor_id,
            action="library.borrow_request_auto_fulfilled_from_waitlist",
            entity_type="book_borrow_request",
            entity_id=next_request.id,
            after={"resulting_issue_id": str(issue.id)},
        )

        borrower_user_id = None
        if next_request.borrower_type == BorrowerType.STUDENT:
            student = await self.session.get(Student, next_request.borrower_id)
            borrower_user_id = student.user_id if student else None
        else:
            employee = await self.session.get(Employee, next_request.borrower_id)
            borrower_user_id = employee.user_id if employee else None

        if borrower_user_id is not None:
            book = await self.book_repo.get_by_id(copy.book_id)
            await create_notification(
                self.session, user_id=borrower_user_id, category=NotificationCategory.LIBRARY,
                title="Your waitlisted book is ready",
                message=f'"{book.title if book else "A book"}" has been issued to you from the waitlist.',
                link="/dashboard/student/library" if next_request.borrower_type == BorrowerType.STUDENT else "/dashboard/faculty/library",
            )

    async def renew(self, issue_id: uuid.UUID, *, actor_id: uuid.UUID) -> BookIssue:
        """Self-service (or staff-assisted) renewal — extends the due
        date by another loan period from its CURRENT due date (not from
        today), capped at MAX_RENEWALS. Deliberately refuses on an
        already-overdue loan: per common library policy, once you're
        overdue you return it (or it gets a fine) rather than quietly
        extending an already-broken due date."""
        issue = await self.issue_repo.get_by_id(issue_id)
        if issue is None:
            raise ResourceNotFoundError("Issue record not found.")
        if issue.status != IssueStatus.ISSUED:
            raise BusinessRuleViolationError(f"This issue is already {issue.status.value.lower()}.")
        if issue.due_date < institute_today():
            raise BusinessRuleViolationError("This loan is overdue and can no longer be renewed — return it instead.")
        if issue.renewal_count >= MAX_RENEWALS:
            raise BusinessRuleViolationError(f"This loan has already been renewed the maximum of {MAX_RENEWALS} times.")

        old_due = issue.due_date
        issue.due_date = issue.due_date + timedelta(days=DEFAULT_LOAN_PERIOD_DAYS)
        issue.renewal_count += 1

        await self.audit.log(
            actor_user_id=actor_id,
            action="library.book_renewed",
            entity_type="book_issue",
            entity_id=issue.id,
            before={"due_date": str(old_due)},
            after={"due_date": str(issue.due_date), "renewal_count": issue.renewal_count},
        )
        return issue

    async def list_active_issues_detailed(self) -> list[dict]:
        issues = await self.issue_repo.list_all_active()
        return await self._enrich(issues)

    async def _enrich(self, issues: list[BookIssue]) -> list[dict]:
        enriched = []
        for issue in issues:
            copy = await self.copy_repo.get_by_id(issue.book_copy_id)
            book = await self.book_repo.get_by_id(copy.book_id) if copy else None
            name = await self.borrower_search.resolve_name(issue.borrower_type, issue.borrower_id)
            enriched.append(
                {
                    "issue": issue,
                    "book_title": book.title if book else "Unknown",
                    "accession_code": copy.accession_code if copy else "?",
                    "borrower_name": name,
                }
            )
        return enriched


def enriched_to_detail(enriched: dict) -> BookIssueDetail:
    """Shared formatting helper — used by both the issue/return router and
    the dashboard service, so there's exactly one place that knows how to
    turn an enriched issue dict into the API response shape."""
    issue = enriched["issue"]
    return BookIssueDetail(
        id=issue.id,
        book_copy_id=issue.book_copy_id,
        borrower_type=issue.borrower_type,
        borrower_id=issue.borrower_id,
        issued_at=issue.issued_at,
        due_date=issue.due_date,
        returned_at=issue.returned_at,
        fine_amount=float(issue.fine_amount) if issue.fine_amount is not None else None,
        status=issue.status,
        book_title=enriched["book_title"],
        accession_code=enriched["accession_code"],
        borrower_name=enriched["borrower_name"],
    )