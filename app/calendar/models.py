"""The academic calendar: holidays, exams, events and term dates everyone can see.

A holiday for students cancels the classes on those days (now, and any timetable generated later);
a holiday for staff excuses those days in staff attendance, so nobody is marked absent."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin

KINDS = ("HOLIDAY", "EXAM", "EVENT", "TERM", "DEADLINE")


class CalendarEvent(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        Index("ix_calendar_events_dates", "start_date", "end_date"),
        CheckConstraint("end_date >= start_date", name="ck_calendar_events_dates_in_order"),
    )

    title: Mapped[str] = mapped_column(String(150), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="EVENT")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    for_students: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    for_staff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)