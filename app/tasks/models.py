"""Tasks: one staff member asks another to do something by a deadline; the receiver accepts or rejects (with a reason) and
completes it; either side can flag it for the administration; everything is recorded so progress can be tracked."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Identity, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class TaskStatus(str, enum.Enum):
    PENDING = "PENDING"        # assigned, waiting for the receiver to accept or reject
    ACCEPTED = "ACCEPTED"      # the receiver is on it
    REJECTED = "REJECTED"      # the receiver said no (with a reason and an explanation)
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"  # the receiver says it is done; the assigner has to confirm (or send it back)
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"    # called off by whoever assigned it (or the administration)


OPEN_STATUSES = (TaskStatus.PENDING, TaskStatus.ACCEPTED)


class TaskPriority(str, enum.Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class RejectReason(str, enum.Enum):
    NO_CAPACITY = "NO_CAPACITY"
    NOT_MY_RESPONSIBILITY = "NOT_MY_RESPONSIBILITY"
    DEADLINE_TOO_SHORT = "DEADLINE_TOO_SHORT"
    MISSING_INFORMATION = "MISSING_INFORMATION"
    CONFLICTS_WITH_OTHER_WORK = "CONFLICTS_WITH_OTHER_WORK"
    OTHER = "OTHER"


REJECT_LABELS = {
    RejectReason.NO_CAPACITY: "I do not have the time or capacity",
    RejectReason.NOT_MY_RESPONSIBILITY: "This is not my responsibility",
    RejectReason.DEADLINE_TOO_SHORT: "The deadline is too short",
    RejectReason.MISSING_INFORMATION: "I am missing information or resources",
    RejectReason.CONFLICTS_WITH_OTHER_WORK: "It conflicts with other work",
    RejectReason.OTHER: "Another reason",
}


class FlagStatus(str, enum.Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class FlagOutcome(str, enum.Enum):
    NO_ACTION = "NO_ACTION"
    TASK_CANCELLED = "TASK_CANCELLED"
    REJECTION_OVERRULED = "REJECTION_OVERRULED"
    DEADLINE_EXTENDED = "DEADLINE_EXTENDED"


class Task(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_assignee", "assignee_user_id"),
        Index("ix_tasks_assigner", "assigner_user_id"),
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_deadline", "deadline"),
    )

    serial_no: Mapped[int] = mapped_column(Integer, Identity(), unique=True, nullable=False)          # TASK-00042
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[TaskPriority] = mapped_column(Enum(TaskPriority, name="task_priority"), nullable=False, default=TaskPriority.NORMAL)
    assigner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    assignee_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus, name="task_status"), nullable=False, default=TaskStatus.PENDING)

    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    original_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)       # what was first agreed
    extension_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)      # accepted or rejected
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[RejectReason | None] = mapped_column(Enum(RejectReason, name="task_reject_reason"), nullable=True)
    reject_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completion_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    follow_up_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)   # re-assigned after a rejection

    # Confirmation by the assigner (migration a9d5c1f4b638)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    rework_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sent_back_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_overruled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskComment(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "task_comments"
    __table_args__ = (Index("ix_task_comments_task", "task_id"),)

    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class TaskFlag(UUIDPKMixin, TimestampMixin, Base):
    """'Please look at this': raised by the person who assigned a task or the person who received it."""

    __tablename__ = "task_flags"
    __table_args__ = (Index("ix_task_flags_task", "task_id"), Index("ix_task_flags_status", "status"))

    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    flagged_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[FlagStatus] = mapped_column(Enum(FlagStatus, name="task_flag_status"), nullable=False, default=FlagStatus.OPEN)
    outcome: Mapped[FlagOutcome | None] = mapped_column(Enum(FlagOutcome, name="task_flag_outcome"), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_automatic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")  # raised by the system when a task is rejected