"""The internship year: final-year interns are current students. Each has one internship record with the hours they must
complete, their placements (a hospital ward and supervisor for a period), daily duty logs (the intern writes them, the
placement's supervisor or the administration verifies), leave (the administration decides) and evaluations. The
administration marks the internship complete; a student whose internship is not complete is not clear to graduate."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Internship(Base):
    __tablename__ = "internships"
    __table_args__ = (Index("uq_internships_one_open", "student_id", unique=True, postgresql_where=text("status = 'ONGOING'")),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), nullable=False, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)                      # planned
    required_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="ONGOING")   # ONGOING | COMPLETED | WITHDRAWN
    completed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class InternPlacement(Base):
    __tablename__ = "intern_placements"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    internship_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("internships.id", ondelete="CASCADE"), nullable=False, index=True)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical_units.id", ondelete="RESTRICT"), nullable=False)
    supervisor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clinical_supervisors.id", ondelete="SET NULL"), nullable=True, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)


class DutyLog(Base):
    __tablename__ = "intern_duty_logs"
    __table_args__ = (UniqueConstraint("internship_id", "day", "shift", name="uq_intern_duty_logs_day_shift"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    internship_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("internships.id", ondelete="CASCADE"), nullable=False, index=True)
    placement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("intern_placements.id", ondelete="RESTRICT"), nullable=False, index=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    shift: Mapped[str] = mapped_column(String(8), nullable=False)                     # MORNING | EVENING | NIGHT
    hours: Mapped[float] = mapped_column(Numeric(4, 1), nullable=False)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(9), nullable=False, default="SUBMITTED")  # SUBMITTED | VERIFIED | REJECTED
    reject_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InternLeave(Base):
    __tablename__ = "intern_leaves"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    internship_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("internships.id", ondelete="CASCADE"), nullable=False, index=True)
    from_date: Mapped[date] = mapped_column(Date, nullable=False)
    to_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(9), nullable=False, default="REQUESTED")  # REQUESTED | APPROVED | REJECTED
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class InternEvaluation(Base):
    __tablename__ = "intern_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    internship_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("internships.id", ondelete="CASCADE"), nullable=False, index=True)
    placement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("intern_placements.id", ondelete="SET NULL"), nullable=True)
    scores: Mapped[dict] = mapped_column(JSON, nullable=False)                         # {criterion: 1..5}
    average: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False)
    comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    evaluated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
