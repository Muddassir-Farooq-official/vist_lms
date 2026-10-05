from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class BookCopyStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    ISSUED = "ISSUED"
    LOST = "LOST"
    DAMAGED = "DAMAGED"
    RETIRED = "RETIRED"  # withdrawn from circulation, not deleted (history stays intact)


class BorrowerType(str, enum.Enum):
    STUDENT = "STUDENT"
    EMPLOYEE = "EMPLOYEE"


class IssueStatus(str, enum.Enum):
    ISSUED = "ISSUED"
    RETURNED = "RETURNED"
    LOST = "LOST"  # borrower never returned it; copy's own status also flips to LOST


class BorrowRequestStatus(str, enum.Enum):
    PENDING = "PENDING"  # awaiting a librarian's decision
    APPROVED = "APPROVED"  # accepted, a copy has been issued (see resulting_issue_id)
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"  # borrower withdrew their own request while it was still PENDING
    # Approved in spirit but no copy was free at the time — auto-fulfilled
    # FIFO (oldest waitlisted first) the next time a copy of this book is
    # returned; see IssueService.return_book(). Not a failure state, just
    # a queue position.
    WAITLISTED = "WAITLISTED"


class Book(UUIDPKMixin, TimestampMixin, Base):
    """Catalogue entry — the title/author/ISBN record. Physical inventory
    (how many copies, their condition/status) lives in BookCopy, one row
    per physical item, not a bare count column — this is what makes
    'inventory' actually mean something: you can always answer 'which
    specific copy is checked out to whom', not just 'how many are out'.

    Physical-only, deliberately (approved decision) — no digital
    edition/PDF/upload concept. cover_image_url is just a catalogue
    thumbnail, not "digital access" to the book itself."""

    __tablename__ = "books"
    __table_args__ = (
        Index("ix_books_title", "title"),
        Index("ix_books_isbn", "isbn"),
        Index("ix_books_is_journal", "is_journal"),
        UniqueConstraint("short_code", name="uq_books_short_code"),
    )

    # System-generated (BK-0001, BK-0002, ...), never admin-supplied —
    # see app/core/sequences.py. Exists so a copy's accession_code can be
    # "<short_code>-C<seq>" (e.g. BK-0007-C003) instead of a random hex
    # fragment — human-typeable and immediately tells you which book AND
    # which copy-of-that-book you're holding, which matters once a
    # single title has dozens of physical copies.
    short_code: Mapped[str] = mapped_column(String(20), nullable=False)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    isbn: Mapped[str | None] = mapped_column(String(20), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(150), nullable=True)
    publication_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shelf_location: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Journals (e.g. Nursing Journals) reuse the exact same catalogue +
    # copy + issue/return machinery as books — a periodical is, for
    # circulation purposes, just a title with different metadata. This
    # flag is what puts it in the "Journals" section of the portal
    # instead of the general book catalogue.
    is_journal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    journal_volume: Mapped[str | None] = mapped_column(String(30), nullable=True)
    journal_issue_number: Mapped[str | None] = mapped_column(String(30), nullable=True)

    cover_image_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    copies: Mapped[list["BookCopy"]] = relationship(back_populates="book", cascade="all, delete-orphan")


class BookCopy(UUIDPKMixin, TimestampMixin, Base):
    """One physical copy of a Book. accession_code is the library's own
    per-copy identifier (auto-generated if the librarian doesn't supply
    one) — this is what a barcode/spine label would carry in a real
    library, and it's what issue/return actually references, not the
    Book row (you issue THIS copy, not 'a copy')."""

    __tablename__ = "book_copies"
    __table_args__ = (
        UniqueConstraint("accession_code", name="uq_book_copies_accession_code"),
        Index("ix_book_copies_book_id", "book_id"),
        Index("ix_book_copies_status", "status"),
    )

    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    accession_code: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[BookCopyStatus] = mapped_column(
        Enum(BookCopyStatus, name="book_copy_status"), default=BookCopyStatus.AVAILABLE, nullable=False
    )
    condition_notes: Mapped[str | None] = mapped_column(String(255), nullable=True)

    book: Mapped[Book] = relationship(back_populates="copies")


class BookIssue(UUIDPKMixin, Base):
    """One issue/return record. Borrower is polymorphic (STUDENT or
    EMPLOYEE) rather than two nullable FK columns, since a book can be
    borrowed by either and the two are mutually exclusive per row —
    this keeps the constraint honest instead of relying on application
    code to never fill in both."""

    __tablename__ = "book_issues"
    __table_args__ = (
        Index("ix_book_issues_copy_id", "book_copy_id"),
        Index("ix_book_issues_borrower", "borrower_type", "borrower_id"),
        Index("ix_book_issues_status", "status"),
        CheckConstraint(
            "(status != 'ISSUED') OR (returned_at IS NULL)", name="issued_records_have_no_return_date"
        ),
    )

    book_copy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("book_copies.id", ondelete="RESTRICT"), nullable=False
    )
    borrower_type: Mapped[BorrowerType] = mapped_column(Enum(BorrowerType, name="borrower_type"), nullable=False)
    borrower_id: Mapped[uuid.UUID] = mapped_column(nullable=False)  # references students.id or employees.id

    issued_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    issued_at: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Sequential self-service renewals — capped at MAX_RENEWALS in
    # IssueService.renew() (ASSUMPTION: 2 renewals, a common library
    # default; adjust the constant if a different policy is wanted).
    renewal_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    returned_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    returned_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    return_condition_notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Manually entered by the librarian on return, not auto-calculated —
    # no overdue-fine formula has been defined (ASSUMPTION — REQUIRES
    # APPROVAL if/when a real fine policy is wanted). Nullable, no charge
    # by default.
    fine_amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)

    status: Mapped[IssueStatus] = mapped_column(
        Enum(IssueStatus, name="issue_status"), default=IssueStatus.ISSUED, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BookBorrowRequest(UUIDPKMixin, Base):
    """A student/faculty self-service ask ('please issue me a copy of
    this title'). The requester never picks a specific copy — that's the
    librarian's call at approval time, same as any real front-desk
    request. Applies to both books and journals (is_journal on the
    referenced Book distinguishes them; the request itself doesn't need
    to know or care)."""

    __tablename__ = "book_borrow_requests"
    __table_args__ = (
        Index("ix_book_borrow_requests_borrower", "borrower_type", "borrower_id"),
        Index("ix_book_borrow_requests_status", "status"),
        Index("ix_book_borrow_requests_book_id", "book_id"),
    )

    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), nullable=False)
    borrower_type: Mapped[BorrowerType] = mapped_column(Enum(BorrowerType, name="borrower_type"), nullable=False)
    borrower_id: Mapped[uuid.UUID] = mapped_column(nullable=False)  # references students.id or employees.id
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)  # borrower's own message, optional

    status: Mapped[BorrowRequestStatus] = mapped_column(
        Enum(BorrowRequestStatus, name="book_borrow_request_status"),
        default=BorrowRequestStatus.PENDING,
        nullable=False,
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resulting_issue_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("book_issues.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)