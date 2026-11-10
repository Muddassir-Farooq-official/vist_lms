"""Exam datesheets (a published timetable of papers per exam), the papers in them, who is on duty
for each paper, the exam roll (separate from class attendance) and cheating (unfair means) cases."""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, time

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, String, Time, UniqueConstraint, func
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
    # Which result type (e.g. "Mid Term") these papers' marks go into, so marks entry can show who was absent.
    assessment_type_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assessment_types.id", ondelete="SET NULL"), nullable=True)
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
    # The teacher in charge of the paper (kept equal to the IN_CHARGE row of exam_duties).
    invigilator_employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)


class DutyRole(str, enum.Enum):
    IN_CHARGE = "IN_CHARGE"    # one per paper: responsible for the hall, the roll and any case
    ASSISTANT = "ASSISTANT"


class ExamDuty(Base):
    """A teacher on duty for one paper."""
    __tablename__ = "exam_duties"
    __table_args__ = (UniqueConstraint("paper_id", "employee_id", name="uq_exam_duties_paper_employee"),
                      Index("ix_exam_duties_employee_id", "employee_id"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exam_papers.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[DutyRole] = mapped_column(Enum(DutyRole, name="exam_duty_role"), nullable=False, default=DutyRole.ASSISTANT)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ExamAttendanceStatus(str, enum.Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LATE = "LATE"


class ExamAttendance(Base):
    """One student's attendance in one paper. Kept apart from class attendance: it never counts towards a course's %."""
    __tablename__ = "exam_attendance"
    __table_args__ = (UniqueConstraint("paper_id", "student_id", name="uq_exam_attendance_paper_student"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exam_papers.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[ExamAttendanceStatus] = mapped_column(Enum(ExamAttendanceStatus, name="exam_attendance_status"), nullable=False)
    marked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    marked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CaseStatus(str, enum.Enum):
    OPEN = "OPEN"          # reported by the teacher on duty; the exam office has not decided yet
    DECIDED = "DECIDED"


class ExamCase(Base):
    """An unfair means (cheating) case reported in the hall. Never deleted: a decision closes it."""
    __tablename__ = "exam_cases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exam_papers.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    note: Mapped[str] = mapped_column(String(1000), nullable=False)
    reported_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    status: Mapped[CaseStatus] = mapped_column(Enum(CaseStatus, name="exam_case_status"), nullable=False, default=CaseStatus.OPEN)
    decision: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
