from __future__ import annotations

import uuid
from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field

from app.attendance.models import AttendanceStatus


class MarkSessionRequest(BaseModel):
    """present_student_ids: everyone currently enrolled and NOT in this
    list is recorded ABSENT -- see AttendanceService.mark_session for
    why this is deliberately all-or-nothing rather than a partial
    per-student PATCH. late_note: required only when an admin marks
    outside the normal window (see AttendanceService.mark_session)."""

    present_student_ids: list[uuid.UUID] = Field(default_factory=list)
    # Everyone enrolled and NOT in this list is ABSENT: attendance is Present or Absent, nothing else.
    late_note: str | None = Field(default=None, max_length=500)


class AttendanceRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    class_session_id: uuid.UUID
    student_id: uuid.UUID
    status: AttendanceStatus
    marked_at: datetime


class AttendanceRecordDetail(AttendanceRecordOut):
    student_name: str
    roll_number: str | None


class StudentAttendancePercentageOut(BaseModel):
    student_id: uuid.UUID
    course_offering_id: uuid.UUID
    total_sessions_taken: int
    present_count: int
    percentage: float
    absent_count: int = 0


class OfferingAttendancePercentagesOut(BaseModel):
    course_offering_id: uuid.UUID
    percentages: dict[uuid.UUID, StudentAttendancePercentageOut]


class LockAttendanceResultOut(BaseModel):
    course_offering_id: uuid.UUID
    enrolled_count: int
    dropped_count: int
    fined_count: int


class OverdueSessionOut(BaseModel):
    id: uuid.UUID
    course_offering_id: uuid.UUID
    course_code: str
    course_title: str
    teacher_name: str | None
    session_date: date
    start_time: time
    room: str


class LockAttendanceRequest(BaseModel):
    """Optional body. early_reason is needed only to lock before the term is
    over (administrator only); it is kept in the audit log."""

    early_reason: str | None = Field(default=None, max_length=500)


class LockPreviewOut(BaseModel):
    course_offering_id: uuid.UUID
    early_reason: str | None = None   # why locking now would be early (None = term is over)
    term_end_date: date | None = None
    already_locked: bool
    can_lock: bool
    enrolled_count: int
    unmarked_overdue_sessions: int
    future_sessions_remaining: int
    would_drop: int
    would_fine: int
    below_eligibility: int
    eligibility_threshold: float
    fine_threshold: float
    drop_threshold: float
    fine_amount: float


class CorrectionRequestCreate(BaseModel):
    reason: str = Field(min_length=5, max_length=500)
    present_student_ids: list[uuid.UUID] = Field(default_factory=list)


class CorrectionDecision(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=500)


class CorrectionRequestOut(BaseModel):
    id: uuid.UUID
    class_session_id: uuid.UUID
    course_offering_id: uuid.UUID
    course_code: str
    course_title: str
    session_date: date
    start_time: time
    requested_by_name: str | None
    reason: str
    status: str
    present_count: int
    decision_note: str | None
    created_at: datetime
    decided_at: datetime | None


class MyAttendanceRecordOut(BaseModel):
    session_date: date
    start_time: time
    status: AttendanceStatus


class AttendanceConfigOut(BaseModel):
    marking_window_minutes: int
    server_now: str  # institute wall-clock, naive ISO (what class times are compared to)
    eligibility_threshold: float
    fine_threshold: float
    drop_threshold: float