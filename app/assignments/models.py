"""Assignments: a teacher sets work for a class with a deadline; each student submits a Google Drive link; the teacher accepts or rejects it."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class SubmissionStatus(str, enum.Enum):
    SUBMITTED = "SUBMITTED"    # handed in, waiting for the teacher
    ACCEPTED = "ACCEPTED"      # the teacher accepted it (final: the student can no longer change it)
    REJECTED = "REJECTED"      # the teacher sent it back with a reason; the student may resubmit until the deadline


class Assignment(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "assignments"
    __table_args__ = (Index("ix_assignments_offering", "course_offering_id"),)

    course_offering_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    # After this moment nobody can submit or change a submission. The teacher can still review.
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AssignmentSubmission(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "assignment_submissions"
    __table_args__ = (
        UniqueConstraint("assignment_id", "student_id", name="uq_submission_assignment_student"),
        Index("ix_submissions_assignment", "assignment_id"),
        Index("ix_submissions_student", "student_id"),
    )

    assignment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    drive_url: Mapped[str] = mapped_column(String(500), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[SubmissionStatus] = mapped_column(
        Enum(SubmissionStatus, name="submission_status"), nullable=False, default=SubmissionStatus.SUBMITTED
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resubmitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resubmission_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)