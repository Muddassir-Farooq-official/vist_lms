"""Daily to-do lists: every member of staff writes down what they plan to do each day and marks how far they got.

The administration reads them month by month (and downloads them). Days before yesterday are locked, so a list is a
record of the day, not something rewritten later. Unfinished items can be carried over to today; the original then
stays on its own day as "carried over", so the history shows it.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class TodoStatus(str, enum.Enum):
    TODO = "TODO"                  # planned, not started
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    NOT_DONE = "NOT_DONE"          # the person says it did not happen (with an optional note)
    CARRIED_OVER = "CARRIED_OVER"  # moved to a later day (a copy is there)


class TodoItem(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "todo_items"
    __table_args__ = (
        Index("ix_todo_items_user_date", "user_id", "work_date"),
        Index("ix_todo_items_date", "work_date"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default=TodoStatus.TODO.value, server_default="TODO")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    carried_from_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("todo_items.id", ondelete="SET NULL"), nullable=True)