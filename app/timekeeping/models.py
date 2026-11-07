"""Staff timekeeping: the timing each employee must keep, and when they actually came and left each day.

A timing is set at three levels and the most specific one wins: the employee's own, then their job role's,
then the institute default. The timing that applied is copied onto every day's row, so changing a timing
later never rewrites what was already recorded. Payroll reads these rows to work out absences, late
arrivals and early leaving.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class StaffTiming(UUIDPKMixin, TimestampMixin, Base):
    """scope DEFAULT (one row), JOB_ROLE (job_role_id) or EMPLOYEE (employee_id)."""

    __tablename__ = "staff_timings"
    __table_args__ = (UniqueConstraint("job_role_id", name="uq_staff_timings_job_role_id"),
                      UniqueConstraint("employee_id", name="uq_staff_timings_employee_id"))

    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    job_role_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_roles.id", ondelete="CASCADE"), nullable=True)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=True)
    in_time: Mapped[time] = mapped_column(Time, nullable=False)
    out_time: Mapped[time] = mapped_column(Time, nullable=False)
    grace_in_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=10)      # relaxation on arrival
    grace_out_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)      # relaxation on leaving
    working_days: Mapped[str] = mapped_column(String(20), nullable=False, default="0,1,2,3,4,5")   # 0 = Monday ... 6 = Sunday


class StaffAttendance(UUIDPKMixin, TimestampMixin, Base):
    """One employee, one day."""

    __tablename__ = "staff_attendance"
    __table_args__ = (UniqueConstraint("employee_id", "work_date", name="uq_staff_attendance_employee_day"),
                      Index("ix_staff_attendance_date", "work_date"))

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    check_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_in: Mapped[time | None] = mapped_column(Time, nullable=True)
    expected_out: Mapped[time | None] = mapped_column(Time, nullable=True)
    grace_in_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    grace_out_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_working_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    late_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)       # after the arrival relaxation
    early_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)      # before the leaving relaxation
    # Official duty / approved leave: the day counts as present and is never late or early.
    excused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    excuse_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="MARKED")    # MARKED, IMPORTED, CORRECTED, GATE (history)
    in_marked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    out_marked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)

class LeaveRequest(UUIDPKMixin, TimestampMixin, Base):
    """A member of staff asks for leave; an administrator approves or refuses it (app/timekeeping/leave.py).
    kind: CASUAL (uses the monthly casual-leave allowance, paid), SICK (paid, a yearly allowance), UNPAID (deducted).
    status: REQUESTED, APPROVED, REJECTED, CANCELLED. Approving marks the days as excused in staff attendance, so the
    person is not counted absent and their classes show in the cover list. See migration d8f0a2b4c6e8."""

    __tablename__ = "leave_requests"
    __table_args__ = (Index("ix_leave_requests_employee", "employee_id"), Index("ix_leave_requests_status", "status"))

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    first_day: Mapped[date] = mapped_column(Date, nullable=False)
    last_day: Mapped[date] = mapped_column(Date, nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)                         # working days in the range
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="REQUESTED")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
