from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.applications.models import ApplicantKind, ApplicationStatus


class TypeIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    for_students: bool = True
    for_employees: bool = True
    requires_dates: bool = False
    requires_attachment: bool = False
    excuses_attendance: bool = False
    yearly_limit_days: int | None = Field(default=None, ge=1, le=366)
    is_active: bool = True
    sort_order: int = 100

    @model_validator(mode="after")
    def _someone_must_be_able_to_apply(self) -> "TypeIn":
        if not (self.for_students or self.for_employees):
            raise ValueError("Choose who can apply for this (students, employees or both).")
        return self


class TypeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    for_students: bool | None = None
    for_employees: bool | None = None
    requires_dates: bool | None = None
    requires_attachment: bool | None = None
    excuses_attendance: bool | None = None
    yearly_limit_days: int | None = Field(default=None, ge=1, le=366)
    clear_limit: bool = False
    is_active: bool | None = None
    sort_order: int | None = None


class Usage(BaseModel):
    """Days of this kind of leave in the year: approved so far, and asked for but not yet decided."""

    year: int
    limit_days: int
    approved_days: int
    pending_days: int


class TypeOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    for_students: bool
    for_employees: bool
    requires_dates: bool
    requires_attachment: bool
    excuses_attendance: bool = False
    yearly_limit_days: int | None
    is_active: bool
    sort_order: int
    usage: Usage | None = None


class AttachmentOut(BaseModel):
    id: uuid.UUID
    original_name: str
    content_type: str
    size_bytes: int
    is_image: bool
    inline: bool


class CommentOut(BaseModel):
    id: uuid.UUID
    side: str                  # APPLICANT or ADMIN
    author_name: str
    body: str
    created_at: datetime


class ApplicantInfo(BaseModel):
    kind: ApplicantKind
    name: str
    identifier: str | None     # roll number or employee code
    detail: str | None         # program and semester, or job title
    contact_no: str | None


class BriefOut(BaseModel):
    """One of the applicant's other applications, for context."""

    id: uuid.UUID
    reference: str
    type_name: str
    subject: str
    status: ApplicationStatus
    from_date: date | None
    to_date: date | None
    created_at: datetime


class ApplicationOut(BaseModel):
    id: uuid.UUID
    reference: str
    type_id: uuid.UUID
    type_name: str
    requires_dates: bool
    subject: str
    details: str
    from_date: date | None
    to_date: date | None
    days: int | None
    status: ApplicationStatus
    awaiting: str | None       # ADMIN (it is the administration's move), APPLICANT (they asked a question) or None (finished)
    decision_note: str | None
    decided_at: datetime | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    attachments: list[AttachmentOut]
    comment_count: int
    comments: list[CommentOut] = []
    applicant: ApplicantInfo | None = None       # shown to the administration
    others: list[BriefOut] = []                  # the applicant's recent applications (administration only)
    usage: Usage | None = None
    stage_label: str | None = None               # where it is now, in plain words ("Forwarded to Exams")
    timeline: list[dict] = []                    # every step so far (the administration also sees the internal notes)
    forwards: list[dict] = []                    # departments it was forwarded to (administration only)


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class Action(str, Enum):
    START_REVIEW = "START_REVIEW"
    REQUEST_INFO = "REQUEST_INFO"
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ReviewIn(BaseModel):
    action: Action
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _say_why(self) -> "ReviewIn":
        needs = {Action.REQUEST_INFO: "Say what you need to know, so the applicant can answer.",
                 Action.REJECT: "Give a reason for rejecting it, so the applicant understands."}
        if self.action in needs and len((self.note or "").strip()) < 3:
            raise ValueError(needs[self.action])
        return self


class BadgeOut(BaseModel):
    count: int


class CountItem(BaseModel):
    key: str
    label: str
    count: int


class WeekPoint(BaseModel):
    week_start: date
    submitted: int
    decided: int


class Kpis(BaseModel):
    total: int
    needs_action: int
    under_review: int
    more_info: int
    approved: int
    rejected: int
    withdrawn: int
    approval_rate: float | None
    avg_decision_hours: float | None
    oldest_waiting_days: int | None


class OverviewOut(BaseModel):
    kpis: Kpis
    by_status: list[CountItem]
    by_type: list[CountItem]
    by_kind: list[CountItem]
    by_week: list[WeekPoint]