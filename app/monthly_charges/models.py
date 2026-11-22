"""Monthly charges prepared as a draft and approved by the accounts office.

The hostel office (hostel rent and mess) or the transport office prepares a month's draft: one line per student with
the amount and how it was worked out. Nothing reaches a student's dues until the accounts office approves the draft;
approving adds each line to the student's dues (a charge paid at the accounts office like any other). A draft can be
discarded and prepared again. An approved line is never charged twice for the same month."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDPKMixin


class ChargeRun(UUIDPKMixin, Base):
    __tablename__ = "charge_runs"
    __table_args__ = (Index("uq_charge_runs_one_draft", "kind", "month", unique=True, postgresql_where=text("status = 'DRAFT'")),)

    kind: Mapped[str] = mapped_column(String(10), nullable=False)               # HOSTEL (rent + mess) | TRANSPORT
    month: Mapped[date] = mapped_column(Date, nullable=False)                    # first day of the month
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="DRAFT")    # DRAFT | APPROVED | DISCARDED
    students: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    prepared_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    prepared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)


class ChargeLine(UUIDPKMixin, Base):
    __tablename__ = "charge_lines"
    __table_args__ = (
        Index("ix_charge_lines_run", "run_id"),
        # once charged (fine_id set), the same thing is never charged again for that month
        Index("uq_charge_lines_charged_once", "kind", "ref_id", "month", unique=True, postgresql_where=text("fine_id IS NOT NULL")),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("charge_runs.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)               # HOSTEL | MESS | TRANSPORT
    ref_id: Mapped[uuid.UUID] = mapped_column(nullable=False)                    # the hostel stay or transport rider row
    month: Mapped[date] = mapped_column(Date, nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    fine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fines.id", ondelete="SET NULL"), nullable=True)
    skipped: Mapped[str | None] = mapped_column(String(120), nullable=True)      # why it was not charged on approval
