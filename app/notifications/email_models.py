"""Email settings (one row), the outbox of emails waiting to go out, and heartbeats of background services.
See migration c5d6e7f8a9b0 for the exact DDL."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EmailSettings(Base):
    __tablename__ = "email_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_email_settings_single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=587, server_default="587")
    security: Mapped[str] = mapped_column(String(10), nullable=False, default="starttls", server_default="starttls")   # starttls | ssl | none
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)          # app/core/secretbox.py, never plain text
    from_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    from_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reply_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notify_by_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")   # also email every notification
    # Every account must prove an email address (one-time code) before using the portal. Off until an administrator turns it on.
    require_verified_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    admin_emails: Mapped[str | None] = mapped_column(Text, nullable=True)            # administration addresses, one per line: a copy of notifications
    admin_copy_categories: Mapped[str | None] = mapped_column(Text, nullable=True)   # comma-separated NotificationCategory values; empty = every kind
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmailOutbox(Base):
    __tablename__ = "email_outbox"
    __table_args__ = (Index("ix_email_outbox_status_next", "status", "next_try_at"),
                      Index("ix_email_outbox_group_key", "group_key", postgresql_where=text("group_key IS NOT NULL")))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    notification_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True)
    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="NOTIFICATION", server_default="NOTIFICATION")   # NOTIFICATION | TEST | ADMIN_COPY | OTP | DIRECT
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDING", server_default="PENDING")        # PENDING | SENT | FAILED
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    next_try_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Administration copies: the same notice to many people becomes ONE email listing who got it.
    group_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    copy_recipients: Mapped[str | None] = mapped_column(Text, nullable=True)       # one name per line


class ServiceHeartbeat(Base):
    __tablename__ = "service_heartbeats"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)


class EmailVerificationCode(Base):
    """A 6-digit code emailed to prove an address. Stored only as a keyed hash; works for 10 minutes, once, 5 tries."""
    __tablename__ = "email_verification_codes"
    __table_args__ = (Index("ix_email_verification_codes_user_created", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())