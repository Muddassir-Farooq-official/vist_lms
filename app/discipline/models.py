"""Warnings to students and staff (app/discipline/service.py, migration a8c0e2f4b6d8).

A warning is never deleted. It counts toward the limit for 12 months; the 3rd one that counts is FINAL and waits for the
admin or principal to decide the action (final chance / suspension / expulsion or termination / fine), which is applied to the
person's record. The person reads it in their portal, acknowledges it, and may reply once.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Identity, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class ViolationType(UUIDPKMixin, TimestampMixin, Base):
    """The dropdown of violations. applies_to: STUDENT | EMPLOYEE | BOTH. Turned off rather than deleted once used."""

    __tablename__ = "violation_types"
    __table_args__ = (UniqueConstraint("applies_to", "name", name="uq_violation_types_applies_to_name"),)

    applies_to: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)


class DisciplinaryWarning(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "disciplinary_warnings"
    __table_args__ = (
        CheckConstraint("(student_id IS NULL) <> (employee_id IS NULL)", name="one_person"),
        Index("ix_disciplinary_warnings_student", "student_id"),
        Index("ix_disciplinary_warnings_employee", "employee_id"),
        Index("ix_disciplinary_warnings_action", "action"),
    )

    serial: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True, nullable=False)      # letter number WRN-<year>-<serial>
    student_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), nullable=True)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=True)
    violation_type_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("violation_types.id", ondelete="SET NULL"), nullable=True)
    violation: Mapped[str] = mapped_column(String(120), nullable=False)                           # as chosen at the time
    note: Mapped[str] = mapped_column(Text, nullable=False)
    incident_date: Mapped[date] = mapped_column(Date, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)                                  # 1, 2, 3 ... among those counting then
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="ACTIVE")             # ACTIVE | WITHDRAWN
    issued_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)   # "I have read this"
    response: Mapped[str | None] = mapped_column(Text, nullable=True)                             # the person's reply / appeal
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdraw_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # final warnings only: PENDING (waiting for a decision) | NONE (final chance) | SUSPEND | EXPEL | TERMINATE | FINE
    action: Mapped[str | None] = mapped_column(String(12), nullable=True)
    action_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fines.id", ondelete="SET NULL"), nullable=True)   # action FINE
    fine_amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    # action SUSPEND: when it was lifted (the person is active again)
    lifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lifted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    lift_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
