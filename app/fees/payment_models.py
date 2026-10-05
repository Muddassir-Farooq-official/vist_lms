from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Enum, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class ChallanStatus(str, enum.Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class PaymentChallan(UUIDPKMixin, TimestampMixin, Base):
    """One payable bill per installment — the 1LINK 1Bill 'Consumer
    Number' concept. Auto-generated the instant a fee structure is
    activated and its installment schedule is created, so every charge is
    immediately payable through the aggregator network with no manual
    accountant step. The student pays at any bank/ATM/mobile-banking
    app/OTC agent connected to 1LINK by referencing this consumer_number,
    and the bank network reports success back to us — the normal path
    never touches cash or a manual entry.

    `provider` records which gateway generated a given challan (currently
    always the simulated one, since VIST hasn't received real 1LINK API
    credentials yet — application is in progress with the bank). Keeping
    this on the row, not just in code, means historical challans stay
    correctly labeled even after a real provider goes live."""

    __tablename__ = "payment_challans"
    __table_args__ = (
        UniqueConstraint("consumer_number", name="uq_payment_challans_consumer_number"),
        Index("ix_payment_challans_assignment_id", "fee_assignment_id"),
        Index("ix_payment_challans_status", "status"),
        CheckConstraint("amount > 0", name="positive_amount"),
    )

    fee_ledger_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fee_ledger_entries.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    fee_assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fee_assignments.id", ondelete="CASCADE"), nullable=False
    )
    consumer_number: Mapped[str] = mapped_column(String(30), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[ChallanStatus] = mapped_column(
        Enum(ChallanStatus, name="challan_status"), default=ChallanStatus.PENDING, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(30), default="1LINK_SIMULATED", nullable=False)
    gateway_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)