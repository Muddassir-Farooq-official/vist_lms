"""The gate: who came in and went out, when, and whether that was late against the timing the administration set."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, Time, UniqueConstraint, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class GateSettings(Base):
    """One row (id = 1): the fixed timing for ALL students, the fine rule, and the default timing for staff with no timing of their own."""

    __tablename__ = "gate_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    # students: one timing for everyone
    student_in_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(8, 30))
    student_out_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    student_grace_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    student_days: Mapped[str] = mapped_column(String(20), nullable=False, default="0,1,2,3,4")          # 0 = Monday ... 6 = Sunday
    student_fine_per_hour: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=100)
    student_max_fine_per_day: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    student_auto_fine: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    # staff: a default for anyone the administration has not given a timing of their own
    staff_default_in: Mapped[time | None] = mapped_column(Time, nullable=True, default=time(9, 0))
    staff_default_out: Mapped[time | None] = mapped_column(Time, nullable=True, default=time(17, 0))
    staff_grace_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    staff_default_days: Mapped[str] = mapped_column(String(20), nullable=False, default="0,1,2,3,4")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now())


class EmployeeSchedule(UUIDPKMixin, TimestampMixin, Base):
    """The timing the administration set for ONE employee (overrides the default)."""

    __tablename__ = "gate_employee_schedules"

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, unique=True)
    in_time: Mapped[time] = mapped_column(Time, nullable=False)
    out_time: Mapped[time] = mapped_column(Time, nullable=False)
    working_days: Mapped[str] = mapped_column(String(20), nullable=False, default="0,1,2,3,4")


class GateLog(UUIDPKMixin, TimestampMixin, Base):
    """One person, one day. The timing that applied is copied onto the row, so changing a timing later never rewrites history."""

    __tablename__ = "gate_logs"
    __table_args__ = (UniqueConstraint("kind", "person_id", "log_date", name="uq_gate_logs_person_day"), Index("ix_gate_logs_date", "log_date"))

    kind: Mapped[str] = mapped_column(String(10), nullable=False)          # STUDENT or EMPLOYEE
    person_id: Mapped[uuid.UUID] = mapped_column(nullable=False)           # students.id or employees.id
    log_date: Mapped[date] = mapped_column(Date, nullable=False)
    check_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_in: Mapped[time | None] = mapped_column(Time, nullable=True)
    expected_out: Mapped[time | None] = mapped_column(Time, nullable=True)
    grace_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_working_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    late_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)       # 0 = on time (or no timing / not a working day)
    early_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)      # left before the end of the day
    in_marked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    out_marked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    fine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fines.id", ondelete="SET NULL"), nullable=True)