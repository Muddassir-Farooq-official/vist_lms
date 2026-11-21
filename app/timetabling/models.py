"""Smart timetable: teachers' unavailable times and limits, and the drafts the generator makes.

A draft is never the live timetable. The live timetable is TimetableSlot / ClassSession (app/academics). Publishing
a draft replaces the live slots of its courses from a chosen date, in one transaction (service.publish).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class TeacherAvailability(UUIDPKMixin, TimestampMixin, Base):
    """One row per teacher who has set anything. blocked: ["MONDAY|<period id>", ...] = cannot teach then.
    max_per_day / max_in_row: this teacher's own limits (NULL = the limits chosen when generating).
    locked: set by an administrator; the teacher can then see but not change it."""

    __tablename__ = "teacher_availability"

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, unique=True)
    blocked: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    max_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_in_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class TimetableDraft(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "timetable_drafts"
    __table_args__ = (Index("ix_timetable_drafts_status", "status"),)

    terms: Mapped[list] = mapped_column(JSONB, nullable=False)              # ["Fall 2026"] or several batch terms
    days: Mapped[list] = mapped_column(JSONB, nullable=False)               # ["MONDAY", ...]
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)   # max_per_day, max_in_row
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="DRAFT", server_default="DRAFT")  # DRAFT, PUBLISHED, DISCARDED
    problems: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)   # what could not be placed, and why
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class TimetableDraftSlot(UUIDPKMixin, TimestampMixin, Base):
    """One class in a draft: a course section (for a combined class, its first section stands for all of them) on a
    day, starting in a period, `span` periods long, in a room. pinned = placed or moved by hand: kept when the draft is
    generated again."""

    __tablename__ = "timetable_draft_slots"
    __table_args__ = (Index("ix_timetable_draft_slots_draft", "draft_id"),)

    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("timetable_drafts.id", ondelete="CASCADE"), nullable=False)
    course_offering_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False)
    day: Mapped[str] = mapped_column(String(10), nullable=False)
    period_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("periods.id", ondelete="CASCADE"), nullable=False)
    span: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    room: Mapped[str] = mapped_column(String(50), nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
