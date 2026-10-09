"""Exam datesheets (a published timetable of papers per exam) and the papers in them."""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, time

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Time, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ScheduleStatus(str, enum.Enum):
    DRAFT = "DRAFT"            # the exam office is still arranging it; nobody else sees it
    PUBLISHED = "PUBLISHED"    # students, teachers and parents see it; admit cards can be printed


class ExamSchedule(Base):
    __tablename__ = "exam_schedules"
    __table_args__ = (UniqueConstraint("term_label", "name", name="uq_exam_schedules_term_name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    term_label: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)          # e.g. "Midterm", "Final"
    status: Mapped[ScheduleStatus] = mapped_column(Enum(ScheduleStatus, name="exam_schedule_status"), nullable=False, default=ScheduleStatus.DRAFT)
    instructions: Mapped[str | None] = mapped_column(String(1000), nullable=True)   # printed on every admit card
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ExamPaper(Base):
    __tablename__ = "exam_papers"
    __table_args__ = (UniqueConstraint("schedule_id", "course_offering_id", name="uq_exam_papers_schedule_offering"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exam_schedules.id", ondelete="CASCADE"), nullable=False, index=True)
    course_offering_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False)
    exam_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    room: Mapped[str] = mapped_column(String(50), nullable=False)
    invigilator_employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)