"""Outgoing messages: every SMS / WhatsApp message the system wants to send, kept as a record (who, what, why, and what happened).

One row per message to one number. When WhatsApp is set up (WhatsAppSettings) it is tried first and SMS is the fallback;
`channel` records which one carried it. `attachment` names a document sent with it on WhatsApp (app/sms/whatsapp.py)."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
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
    # WhatsApp (migration f7b9d1e3a5c7)
    channel: Mapped[str | None] = mapped_column(String(10), nullable=True)            # WHATSAPP | SMS: what carried it
    attachment: Mapped[str | None] = mapped_column(String(80), nullable=True)         # CHALLAN:<id> | RECEIPT:<id> | RESULT:<student id> | CERTIFICATE:<id>
    whatsapp_error: Mapped[str | None] = mapped_column(String(300), nullable=True)    # why WhatsApp did not take it (then SMS was used)


class WhatsAppSettings(Base):
    """Settings -> SMS & WhatsApp: how WhatsApp messages are sent (one row). Secrets are sealed (app/core/secretbox.py).
    mode: OFF | META (WhatsApp Cloud API: approved templates) | LINK (any provider that takes a plain web request)."""

    __tablename__ = "whatsapp_settings"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="OFF", server_default="OFF")
    send_documents: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    meta_phone_number_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    meta_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_template_text: Mapped[str | None] = mapped_column(String(100), nullable=True)       # body {{1}} = the message
    meta_template_document: Mapped[str | None] = mapped_column(String(100), nullable=True)   # document header + body {{1}}
    meta_template_language: Mapped[str] = mapped_column(String(10), nullable=False, default="en", server_default="en")
    link_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    link_method: Mapped[str] = mapped_column(String(6), nullable=False, default="GET", server_default="GET")
    link_body: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    link_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    link_sender: Mapped[str | None] = mapped_column(String(60), nullable=True)
    link_success_marker: Mapped[str | None] = mapped_column(String(100), nullable=True)
    public_base: Mapped[str | None] = mapped_column(String(200), nullable=True)   # the portal's public address, for document links
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)