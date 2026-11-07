from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class AttendanceStatus(str, enum.Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LEAVE = "LEAVE"        # approved leave (e.g. medical): not counted for or against the student


class AttendanceRecord(UUIDPKMixin, TimestampMixin, Base):
    """One student's attendance for one ClassSession (a generated,
    dated occurrence of a TimetableSlot -- see app/academics/models.py).
    A session's attendance is all-or-nothing once submitted:
    AttendanceService.mark_session() requires every currently-ENROLLED
    student to be explicitly marked PRESENT or ABSENT in the same call
    -- there is no "unmarked" state to leave a gap in. A student's
    attendance % is present-count over sessions-where-attendance-was-
    actually-taken, not over every session ever scheduled -- a session
    nobody took attendance for (or a CANCELLED one) simply has no
    AttendanceRecord rows and doesn't count against anyone.

    Editable by the marking teacher or an admin until either the
    offering's term concludes (CourseOfferingService.end_semester) or
    AttendanceService.lock_attendance() fires for that offering --
    whichever happens first."""

    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint("class_session_id", "student_id", name="uq_attendance_records_session_student"),
        Index("ix_attendance_records_student_id", "student_id"),
        Index("ix_attendance_records_class_session_id", "class_session_id"),
    )

    class_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[AttendanceStatus] = mapped_column(Enum(AttendanceStatus, name="attendance_status"), nullable=False)
    marked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    marked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AttendanceCorrectionRequest(UUIDPKMixin, TimestampMixin, Base):
    """A teacher asks the admin to record/correct a class's attendance after
    the marking window closed (teachers cannot do it themselves). Carries the
    proposed register so the admin only has to approve; approval applies it
    exactly as an admin late-marking with the request's reason as the note."""

    __tablename__ = "attendance_correction_requests"
    __table_args__ = (
        Index("ix_attendance_correction_requests_status", "status"),
        Index("ix_attendance_correction_requests_session", "class_session_id"),
    )

    class_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"), nullable=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    present_student_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")  # PENDING | APPROVED | REJECTED
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

class ShortfallStatus(str, enum.Enum):
    FINED = "FINED"                          # charged the fine for its band; paying (or a waiver) lets the student pass on marks
    AWAITING_DECISION = "AWAITING_DECISION"  # below the lowest band: an administrator decides (drop, allow with a fine, or excuse)
    ALLOWED_WITH_FINE = "ALLOWED_WITH_FINE"  # decided: may continue once the fine is paid
    EXCUSED = "EXCUSED"                      # decided: may continue, no fine (e.g. medical reasons)
    DROPPED = "DROPPED"                      # decided: withdrawn from the course


class AttendanceShortfall(UUIDPKMixin, TimestampMixin, Base):
    """One student's attendance shortfall in one course (graded attendance fines). One row per student and course:
    re-running the fines only charges the difference when attendance has fallen into a worse band. Fines charged for it
    point back here (fines.related_entity_type = 'attendance_shortfall'). See migration b6d8f0a2c4e6."""

    __tablename__ = "attendance_shortfalls"
    __table_args__ = (
        UniqueConstraint("student_id", "course_offering_id", name="uq_attendance_shortfall_student_offering"),
        Index("ix_attendance_shortfalls_status", "status"),
        Index("ix_attendance_shortfalls_term", "term_label"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    course_offering_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False)
    term_label: Mapped[str] = mapped_column(String(50), nullable=False)
    percentage: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)        # when last evaluated
    band: Mapped[int] = mapped_column(Integer, nullable=False)                       # 1..3 = fine bands, 4 = below the lowest band
    amount_charged: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(20), nullable=False)                 # a ShortfallStatus value
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
