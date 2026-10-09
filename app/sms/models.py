"""Outgoing SMS: every message the system wants to send, kept as a record (who, what, why, and what happened)."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SmsStatus(str, enum.Enum):
    QUEUED = "QUEUED"      # waiting for the sender
    SENT = "SENT"          # the provider accepted it
    FAILED = "FAILED"      # the provider refused it after every retry (see last_error)
    LOGGED = "LOGGED"      # no SMS provider configured: recorded but not sent
    SKIPPED = "SKIPPED"    # nothing to send to (no valid mobile number)


class SmsMessage(Base):
    __tablename__ = "sms_messages"
    __table_args__ = (Index("ix_sms_messages_status_created", "status", "created_at"),
                      Index("ix_sms_messages_student_event", "student_id", "event"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    to_number: Mapped[str] = mapped_column(String(20), nullable=False)        # as stored on the record
    recipient: Mapped[str] = mapped_column(String(20), nullable=False)        # STUDENT | GUARDIAN | TEST
    body: Mapped[str] = mapped_column(String(320), nullable=False)
    event: Mapped[str] = mapped_column(String(40), nullable=False)            # ATTENDANCE_FINE | DATESHEET | RESULTS | FEE_OVERDUE | TEST
    student_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[SmsStatus] = mapped_column(Enum(SmsStatus, name="sms_status"), nullable=False, default=SmsStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)