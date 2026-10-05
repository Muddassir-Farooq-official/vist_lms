from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.library.models import BorrowerType, BorrowRequestStatus


class BorrowRequestCreate(BaseModel):
    """Submitted by the logged-in student/faculty member themselves — the
    borrower is always resolved server-side from their own session
    (see app/library/portal_router.py), never taken from the request
    body, so nobody can submit a request on someone else's behalf."""

    book_id: uuid.UUID
    note: str | None = Field(default=None, max_length=255)


class BorrowRequestDecision(BaseModel):
    note: str | None = Field(default=None, max_length=255)


class BorrowRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    book_id: uuid.UUID
    borrower_type: BorrowerType
    borrower_id: uuid.UUID
    note: str | None
    status: BorrowRequestStatus
    decided_at: datetime | None
    decision_note: str | None
    resulting_issue_id: uuid.UUID | None
    created_at: datetime


class BorrowRequestDetail(BorrowRequestOut):
    book_title: str
    is_journal: bool
    borrower_name: str
    # For better librarian tracking — "which class/semester is this
    # student in" (or department, for staff) so requests from students
    # sharing a common name aren't ambiguous.
    borrower_identifier: str | None = None  # roll number or employee code
    borrower_program: str | None = None
    borrower_semester: int | None = None
    borrower_department: str | None = None


# ---------------------------------------------------------------------------
# Self-service portal ("my loans")
# ---------------------------------------------------------------------------
class MyLoanOut(BaseModel):
    issue_id: uuid.UUID
    book_id: uuid.UUID
    book_title: str
    is_journal: bool
    accession_code: str
    issued_at: date
    due_date: date
    returned_at: date | None
    is_overdue: bool
    status: str
    renewal_count: int
    can_renew: bool


class MyLoansResponse(BaseModel):
    active: list[MyLoanOut]
    history: list[MyLoanOut]