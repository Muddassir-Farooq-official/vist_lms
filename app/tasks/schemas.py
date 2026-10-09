from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from app.tasks.models import FlagOutcome, FlagStatus, RejectReason, TaskPriority, TaskStatus


class StaffOut(BaseModel):
    user_id: uuid.UUID
    name: str
    detail: str | None
    identifier: str | None


class TaskCreate(BaseModel):
    assignee_user_id: uuid.UUID
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=3, max_length=5000)
    priority: TaskPriority = TaskPriority.NORMAL
    deadline: datetime
    follow_up_of_id: uuid.UUID | None = None


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, min_length=3, max_length=5000)
    priority: TaskPriority | None = None
    deadline: datetime | None = None


class Response(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


class RespondIn(BaseModel):
    action: Response
    reason: RejectReason | None = None
    explanation: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _a_rejection_must_be_explained(self) -> "RespondIn":
        if self.action == Response.REJECT:
            if self.reason is None:
                raise ValueError("Choose the reason you are rejecting this task.")
            if len((self.explanation or "").strip()) < 10:
                raise ValueError("Explain why (at least a sentence), so the person who assigned it understands.")
        return self


class CompleteIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class SendBackIn(BaseModel):
    note: str = Field(min_length=5, max_length=2000)
    new_deadline: datetime | None = None


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class FlagIn(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)


class ResolveIn(BaseModel):
    outcome: FlagOutcome
    note: str = Field(min_length=3, max_length=2000)
    new_deadline: datetime | None = None

    @model_validator(mode="after")
    def _an_extension_needs_a_date(self) -> "ResolveIn":
        if self.outcome == FlagOutcome.DEADLINE_EXTENDED and self.new_deadline is None:
            raise ValueError("Choose the new deadline.")
        return self


class PersonRef(BaseModel):
    user_id: uuid.UUID
    name: str
    detail: str | None


class CommentOut(BaseModel):
    id: uuid.UUID
    author_user_id: uuid.UUID | None
    author_name: str
    body: str
    created_at: datetime


class FlagOut(BaseModel):
    id: uuid.UUID
    flagged_by_name: str
    flagged_by_user_id: uuid.UUID | None
    reason: str
    status: FlagStatus
    outcome: FlagOutcome | None
    resolution_note: str | None
    resolved_by_name: str | None
    resolved_at: datetime | None
    created_at: datetime


class Abilities(BaseModel):
    """What the signed-in person may do with this task right now (so the screen never offers something that would be refused)."""

    accept: bool = False
    reject: bool = False
    complete: bool = False
    cancel: bool = False
    edit: str | None = None            # ALL (before it is accepted), DEADLINE (after), or None
    flag: bool = False
    comment: bool = False
    assign_again: bool = False
    resolve_flag: bool = False
    confirm: bool = False              # the giver confirms a completed task is really done
    send_back: bool = False            # ... or sends it back


class FollowUp(BaseModel):
    id: uuid.UUID
    reference: str


class TaskOut(BaseModel):
    id: uuid.UUID
    reference: str
    title: str
    description: str
    priority: TaskPriority
    status: TaskStatus
    assigner: PersonRef
    assignee: PersonRef
    deadline: datetime
    original_deadline: datetime
    extension_count: int
    created_at: datetime
    responded_at: datetime | None
    accepted_at: datetime | None
    reject_reason: RejectReason | None
    reject_reason_label: str | None
    reject_explanation: str | None
    completed_at: datetime | None
    completion_note: str | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    is_overdue: bool                         # still open, and the deadline has passed
    timeliness: str | None                   # for a completed task: ON_TIME or LATE
    late_by_hours: float | None
    is_flagged: bool
    comment_count: int
    follow_up_of: FollowUp | None
    my_role: str                             # ASSIGNER, ASSIGNEE or ADMIN
    can: Abilities
    comments: list[CommentOut] = []
    flags: list[FlagOut] = []


class SummaryOut(BaseModel):
    needs_response: int           # waiting for ME to accept or reject
    overdue: int                  # mine, still open, deadline passed
    open: int                     # mine, accepted and not yet done
    open_flags: int               # (administration) tasks waiting for a decision
    badge: int


# ---------------------------------------------------------------- progress reports
class ReasonCount(BaseModel):
    key: str
    label: str
    count: int


class WeekPoint(BaseModel):
    week_start: date
    assigned: int
    completed: int
    rejected: int


class ReceiverStats(BaseModel):
    """A person as the one who RECEIVES tasks."""

    received: int
    accepted: int
    rejected: int
    completed: int
    awaiting_confirmation: int = 0  # completed by the receiver, not yet confirmed by the giver
    sent_back: int = 0
    completed_on_time: int
    completed_late: int
    overdue: int                  # open and past the deadline (including tasks never answered)
    still_open: int               # open and not yet due
    cancelled: int
    pending_response: int         # not yet accepted/rejected (and not overdue)
    acceptance_rate: float | None     # accepted / (accepted + rejected)
    on_time_rate: float | None        # completed on time / (completed + overdue)
    avg_response_hours: float | None  # from being assigned to accept/reject
    avg_completion_hours: float | None  # from accepting to completing


class AssignerStats(BaseModel):
    """A person as the one who ASSIGNS tasks."""

    assigned: int
    rejected: int
    completed: int                # confirmed as done
    awaiting_confirmation: int = 0  # done by the receiver, waiting for MY confirmation
    overdue: int
    cancelled: int
    flagged: int


class StaffRow(BaseModel):
    user_id: uuid.UUID
    name: str
    detail: str | None
    receiving: ReceiverStats
    assigning: AssignerStats


class StaffReport(BaseModel):
    days: int
    since: datetime | None
    totals: ReceiverStats
    flags_open: int
    flags_resolved: int
    reasons: list[ReasonCount]
    weeks: list[WeekPoint]
    rows: list[StaffRow]


class PersonReport(BaseModel):
    days: int
    since: datetime | None
    person: PersonRef
    receiving: ReceiverStats
    assigning: AssignerStats
    reasons: list[ReasonCount]
    weeks: list[WeekPoint]
    rejected: list[TaskOut]          # what they turned down, and why
    late: list[TaskOut]              # completed late, or still open past the deadline
    flagged: list[TaskOut]