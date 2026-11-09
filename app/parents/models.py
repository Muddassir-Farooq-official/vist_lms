"""A parent/guardian login linked to the students they may follow."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ParentStudentLink(Base):
    __tablename__ = "parent_student_links"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

class ParentAlertState(Base):
    """What the last Monday note to a student's parents said (migration d5f7b9c1e3a5), so a note goes out only when
    something changed."""

    __tablename__ = "parent_alert_states"

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    signature: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    sent_on: Mapped[date | None] = mapped_column(Date, nullable=True)
