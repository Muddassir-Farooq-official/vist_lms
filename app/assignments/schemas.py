from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.assignments.models import SubmissionStatus


class AssignmentCreate(BaseModel):
    course_offering_id: uuid.UUID
    title: str = Field(min_length=3, max_length=200)
    instructions: str = Field(min_length=3, max_length=5000)
    deadline: datetime


class AssignmentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    instructions: str | None = Field(default=None, min_length=3, max_length=5000)
    deadline: datetime | None = None


class SubmitRequest(BaseModel):
    drive_url: str = Field(min_length=1, max_length=500)
    note: str | None = Field(default=None, max_length=500)
    acknowledge_private: bool = False        # "yes, I know the link looks private, submit it anyway"


class Decision(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class ReviewRequest(BaseModel):
    decision: Decision
    feedback: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _a_rejection_needs_a_reason(self) -> "ReviewRequest":
        if self.decision == Decision.REJECTED and len((self.feedback or "").strip()) < 3:
            raise ValueError("Say why you are rejecting it, so the student knows what to fix.")
        return self


class SubmissionOut(BaseModel):
    id: uuid.UUID
    drive_url: str
    note: str | None
    status: SubmissionStatus
    submitted_at: datetime
    resubmitted_at: datetime | None
    resubmission_count: int
    feedback: str | None
    reviewed_at: datetime | None


class AssignmentBase(BaseModel):
    id: uuid.UUID
    course_offering_id: uuid.UUID
    course_code: str
    course_title: str
    section_label: str
    term_label: str
    title: str
    instructions: str
    deadline: datetime
    created_at: datetime
    is_open: bool


class AssignmentStudentOut(AssignmentBase):
    teacher_name: str | None
    # NOT_SUBMITTED, SUBMITTED, ACCEPTED, REJECTED, or MISSED (the deadline passed and nothing was handed in)
    state: str
    can_submit: bool
    submission: SubmissionOut | None


class AssignmentTeacherOut(AssignmentBase):
    enrolled: int
    submitted: int            # students who have handed something in
    awaiting_review: int
    accepted: int
    rejected: int
    not_submitted: int


class RosterRow(BaseModel):
    student_id: uuid.UUID
    student_name: str
    roll_number: str | None
    state: str                # NOT_SUBMITTED / SUBMITTED / ACCEPTED / REJECTED
    submission_id: uuid.UUID | None
    drive_url: str | None
    note: str | None
    submitted_at: datetime | None
    resubmission_count: int
    feedback: str | None
    reviewed_at: datetime | None