"""Fee refunds: requested by the accounts office, approved by an administrator (a different person), then paid out."""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RefundKind(str, enum.Enum):
    OVERPAYMENT = "OVERPAYMENT"    # more was paid than is owed (e.g. a scholarship came after payment): give back the credit
    WITHDRAWAL = "WITHDRAWAL"      # the student left: part of the fee is written off and that amount given back
    OTHER = "OTHER"


class RefundStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"      # called off before it was paid; any write-off was put back


class FeeRefund(Base):
    __tablename__ = "fee_refunds"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    fee_assignment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fee_assignments.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[RefundKind] = mapped_column(Enum(RefundKind, name="fee_refund_kind"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)               # to pay back
    write_off: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0, server_default="0")  # charges cancelled on approval
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[RefundStatus] = mapped_column(Enum(RefundStatus, name="fee_refund_status"), nullable=False, default=RefundStatus.REQUESTED)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    paid_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    ledger_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fee_ledger_entries.id", ondelete="SET NULL"), nullable=True)
    voucher_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)