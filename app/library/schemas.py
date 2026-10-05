from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.library.models import BookCopyStatus, BorrowerType, IssueStatus


def _validate_url(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    v = v.strip()
    if not (v.startswith("http://") or v.startswith("https://")):
        raise ValueError("Must be a full URL starting with http:// or https://.")
    return v


# ---------------------------------------------------------------------------
# Books / Copies
#
# Physical-only, deliberately — the library catalogue tracks real
# physical copies with accession codes; there is no digital
# file/PDF/upload concept here (see migration e5a9c1f04b3d for the
# removal and why).
# ---------------------------------------------------------------------------
class BookCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    author: str = Field(min_length=1, max_length=255)
    isbn: str | None = Field(default=None, max_length=20)
    category: str | None = Field(default=None, max_length=100)
    publisher: str | None = Field(default=None, max_length=150)
    publication_year: int | None = Field(default=None, ge=1000, le=2100)
    shelf_location: str | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=1000)
    initial_copies: int = Field(default=1, ge=0, le=500)
    cover_image_url: str | None = Field(default=None, max_length=1000)
    is_journal: bool = False
    journal_volume: str | None = Field(default=None, max_length=30)
    journal_issue_number: str | None = Field(default=None, max_length=30)

    @field_validator("cover_image_url")
    @classmethod
    def _validate_urls(cls, v: str | None) -> str | None:
        return _validate_url(v)


class BookUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    author: str | None = Field(default=None, min_length=1, max_length=255)
    isbn: str | None = Field(default=None, max_length=20)
    category: str | None = Field(default=None, max_length=100)
    publisher: str | None = Field(default=None, max_length=150)
    publication_year: int | None = Field(default=None, ge=1000, le=2100)
    shelf_location: str | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=1000)
    cover_image_url: str | None = Field(default=None, max_length=1000)
    is_journal: bool | None = None
    journal_volume: str | None = Field(default=None, max_length=30)
    journal_issue_number: str | None = Field(default=None, max_length=30)

    @field_validator("cover_image_url")
    @classmethod
    def _validate_urls(cls, v: str | None) -> str | None:
        return _validate_url(v)


class BookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    short_code: str
    title: str
    author: str
    isbn: str | None
    category: str | None
    publisher: str | None
    publication_year: int | None
    shelf_location: str | None
    description: str | None
    cover_image_url: str | None
    is_journal: bool
    journal_volume: str | None
    journal_issue_number: str | None


class BookWithCounts(BookOut):
    total_copies: int
    available_copies: int


class BookCopyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    book_id: uuid.UUID
    accession_code: str
    status: BookCopyStatus
    condition_notes: str | None


class AddCopiesRequest(BaseModel):
    count: int = Field(gt=0, le=200)


class CopyStatusUpdateRequest(BaseModel):
    status: BookCopyStatus
    condition_notes: str | None = Field(default=None, max_length=255)


# ---------------------------------------------------------------------------
# Borrower search (minimal — name/id only, not full student/employee
# records, so LIBRARY_ISSUE doesn't implicitly grant broader visibility)
# ---------------------------------------------------------------------------
class BorrowerSearchResult(BaseModel):
    borrower_type: BorrowerType
    borrower_id: uuid.UUID
    display_name: str
    identifier: str
    # Can this person borrow right now? If not, why -- shown to the librarian BEFORE they try.
    can_borrow: bool = True
    blocked_reason: str | None = None
    active_loans: int = 0
    loan_limit: int = 0


# ---------------------------------------------------------------------------
# Issue / Return
# ---------------------------------------------------------------------------
class IssueBookRequest(BaseModel):
    book_copy_id: uuid.UUID
    borrower_type: BorrowerType
    borrower_id: uuid.UUID
    due_date: date | None = None


class ReturnPreviewOut(BaseModel):
    """What returning this book today would cost, worked out before the librarian confirms."""

    issue_id: uuid.UUID
    book_title: str
    borrower_name: str
    due_date: date
    days_overdue: int
    late_fee_per_day: float
    suggested_fine: float


class ReturnBookRequest(BaseModel):
    condition_notes: str | None = Field(default=None, max_length=255)
    is_lost: bool = False
    fine_amount: float | None = Field(default=None, ge=0)


class BookIssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    book_copy_id: uuid.UUID
    borrower_type: BorrowerType
    borrower_id: uuid.UUID
    issued_at: date
    due_date: date
    returned_at: date | None
    fine_amount: float | None
    status: IssueStatus


class BookIssueDetail(BookIssueOut):
    book_title: str
    accession_code: str
    borrower_name: str


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
class LibraryDashboardResponse(BaseModel):
    total_titles: int
    total_copies: int
    available_copies: int
    issued_copies: int
    overdue_count: int
    pending_requests_count: int
    recent_issues: list[BookIssueDetail]
    recent_returns: list[BookIssueDetail]