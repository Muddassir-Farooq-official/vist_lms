from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class NotificationCategory(str, enum.Enum):
    """What kind of event triggered this — lets the frontend show a
    different icon/color per category and lets a person filter their
    list. Extend freely; nothing else needs to change to add one."""

    LIBRARY = "LIBRARY"
    FINE = "FINE"
    COMPLAINT = "COMPLAINT"
    FEE = "FEE"
    PAYROLL = "PAYROLL"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    ASSIGNMENT = "ASSIGNMENT"
    APPLICATION = "APPLICATION"
    TASK = "TASK"
    RESULT = "RESULT"
    FINANCE = "FINANCE"
    GENERAL = "GENERAL"


class Notification(UUIDPKMixin, TimestampMixin, Base):
    """In-app notification, tied directly to a user_id (the login
    account), not a student/employee record — a person sees their
    notifications regardless of which domain role they're acting in.

    This is deliberately the REAL, working notification feature for
    this project: there is no SMTP/SMS provider configured anywhere in
    this system (see app/notifications/email_service.py for the
    stub that's ready to wire up once real credentials exist), so an
    in-app notification is the honest thing to build now rather than
    silently doing nothing. When real email/SMS credentials do exist,
    the trigger points that call NotificationService.create() below
    are also the natural place to additionally call EmailService.send()
    — no restructuring needed, just one more call at each site."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_id", "user_id"), Index("ix_notifications_is_read", "is_read"),
                      Index("ix_notifications_created_at", "created_at"))

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[NotificationCategory] = mapped_column(
        Enum(NotificationCategory, name="notification_category"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    # Relative in-app URL (e.g. "/dashboard/student/fines") the
    # notification should navigate to when clicked — optional, since a
    # purely informational notification has nowhere useful to send you.
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When it was pushed to the person's phones / browsers (app/push). Empty = not yet (or push is off).
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

from app.notifications import email_models as _email_models  # noqa: E402,F401  (registers email tables with Base)