"""Committees and their meetings: agenda, minutes, decisions and action items.

Only a committee's members and administrators see its meetings and minutes; the person an action item is given to also
sees that item. The chair or secretary schedules meetings, writes the minutes and gives action items; the chair (or an
administrator) makes the minutes final, after which they can no longer change. Action items remind their owner until
they are done."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, Text, Time, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Committee(Base):
    __tablename__ = "committees"
    __table_args__ = (UniqueConstraint("name", name="uq_committees_name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    purpose: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CommitteeMember(Base):
    __tablename__ = "committee_members"

    committee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("committees.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="MEMBER")       # CHAIR | SECRETARY | MEMBER


class CommitteeMeeting(Base):
    __tablename__ = "committee_meetings"
    __table_args__ = (Index("ix_committee_meetings_committee", "committee_id", "held_on"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    committee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("committees.id", ondelete="CASCADE"), nullable=False)
    held_on: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    place: Mapped[str | None] = mapped_column(String(120), nullable=True)
    agenda: Mapped[str] = mapped_column(Text, nullable=False)
    minutes: Mapped[str | None] = mapped_column(Text, nullable=True)
    decisions: Mapped[str | None] = mapped_column(Text, nullable=True)
    attended: Mapped[str | None] = mapped_column(String(2000), nullable=True)                 # names, as written by the secretary
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="SCHEDULED")      # SCHEDULED | FINAL | CANCELLED
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finalised_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    finalised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActionItem(Base):
    __tablename__ = "committee_actions"
    __table_args__ = (Index("ix_committee_actions_owner", "owner_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("committee_meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="OPEN")            # OPEN | DONE | DROPPED
    done_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminded_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
