"""Student mentoring: each student has a teacher as mentor. The mentor records meetings (private notes: only the mentor
and administrators read them), agreed actions and a follow-up date, and can refer the student to the principal.
Students, parents and other teachers never see the notes; a student and their parents see only who the mentor is."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MentorAssignment(Base):
    __tablename__ = "mentor_assignments"
    __table_args__ = (Index("uq_mentor_assignments_one_current", "student_id", unique=True, postgresql_where=text("end_date IS NULL")),
                      Index("ix_mentor_assignments_mentor", "mentor_id"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    mentor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class MentorMeeting(Base):
    __tablename__ = "mentor_meetings"
    __table_args__ = (Index("ix_mentor_meetings_student", "student_id", "met_on"), Index("ix_mentor_meetings_referral", "referral_status"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    mentor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    met_on: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="MEETING")      # MEETING | PHONE | PARENTS
    notes: Mapped[str] = mapped_column(Text, nullable=False)                               # private: mentor + administrators
    actions: Mapped[str | None] = mapped_column(String(1000), nullable=True)               # what was agreed
    follow_up_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    referral_status: Mapped[str | None] = mapped_column(String(10), nullable=True)         # OPEN | RESOLVED (referred to the principal)
    referral_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")   # kept, hidden (wrong student)
