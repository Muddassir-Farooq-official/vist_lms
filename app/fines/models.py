from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class FinePayerType(str, enum.Enum):
    STUDENT = "STUDENT"
    EMPLOYEE = "EMPLOYEE"


class FineSource(str, enum.Enum):
    """What kind of fine this is — lets admin/accounts filter and report
    by origin even though the module itself is generic. Extend this list
    as new fine-issuing contexts show up; nothing else needs to change
    to add a new source."""

    LIBRARY = "LIBRARY"
    DISCIPLINARY = "DISCIPLINARY"
    FEE_LATE_PAYMENT = "FEE_LATE_PAYMENT"
    ATTENDANCE = "ATTENDANCE"
    GATE = "GATE"                       # late arrival, marked at the gate
    INVENTORY = "INVENTORY"             # an item from the store, issued now and paid later
    HOSTEL = "HOSTEL"                   # the monthly hostel charge
    OTHER = "OTHER"


class FineStatus(str, enum.Enum):
    # A librarian/accounts-officer request awaiting admin review — NOT
    # yet a real charge. Invisible to the payer (see
    # FineRepository.list_visible_to_payer) until an admin approves it.
    PENDING_APPROVAL = "PENDING_APPROVAL"
    # Admin denied the request — final state, same as WAIVED/PAID.
    # The payer was never shown this and is never notified of it.
    REJECTED = "REJECTED"
    PENDING = "PENDING"
    PAID = "PAID"
    WAIVED = "WAIVED"  # forgiven, not collected — distinct from PAID for reporting accuracy


class Fine(UUIDPKMixin, TimestampMixin, Base):
    """A single, ad-hoc, reason-driven fine against a student or
    employee. Deliberately NOT modeled as a Fee/FeeAssignment/Challan —
    those are structured, admin-defined, installment-based charges,
    whereas a fine is "librarian returns a book late, types in an
    amount and a reason, right now." Same tracked-balance discipline as
    Fees (status, who resolved it, when) without the installment-plan
    machinery that doesn't apply here.

    Scalable by design (approved decision): any role holding
    FINE_ISSUE can raise one against any student or employee — not
    scoped to a single module. `source` records which context it came
    from for reporting, but doesn't restrict who can issue what."""

    __tablename__ = "fines"
    __table_args__ = (
        Index("ix_fines_payer", "payer_type", "payer_id"),
        Index("ix_fines_status", "status"),
        Index("ix_fines_source", "source"),
    )

    payer_type: Mapped[FinePayerType] = mapped_column(Enum(FinePayerType, name="fine_payer_type"), nullable=False)
    payer_id: Mapped[uuid.UUID] = mapped_column(nullable=False)  # references students.id or employees.id

    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[FineSource] = mapped_column(Enum(FineSource, name="fine_source"), nullable=False)

    status: Mapped[FineStatus] = mapped_column(
        Enum(FineStatus, name="fine_status"), default=FineStatus.PENDING, nullable=False
    )

    issued_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Who/when approved a PENDING_APPROVAL request into a real,
    # chargeable PENDING fine. Set immediately (equal to issued_by/
    # issued_at) when an admin issues a fine directly rather than via
    # the request workflow — every fine that has ever been chargeable
    # has these set, whichever path created it.
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Optional traceability back to the thing that caused the fine, e.g.
    # a book_issues.id for a library late fee — intentionally untyped
    # (no FK) since the source could be any module now or in the future;
    # this is a reporting breadcrumb, not a referential constraint.
    # How the money came in (set when marked PAID) -- counted as income in Accounts.
    payment_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    receipt_number: Mapped[str | None] = mapped_column(String(30), nullable=True, unique=True)

    related_entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    related_entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

class FinePaymentReversal(UUIDPKMixin, Base):
    """A fine payment taken back (recorded against the wrong person, a bounced
    cheque...). The fine goes back to unpaid; this row keeps the cancelled
    receipt so the books show the money in on the day it came and out on the
    day it was reversed."""

    __tablename__ = "fine_payment_reversals"
    __table_args__ = (Index("ix_fine_payment_reversals_fine_id", "fine_id"),)

    fine_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fines.id", ondelete="RESTRICT"), nullable=False)
    receipt_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reversed_on: Mapped[date] = mapped_column(Date, nullable=False)
    reversed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reversed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)