"""A phone or browser that asked for push notifications (Web Push). One person can have several devices."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    __table_args__ = (Index("ix_push_subscriptions_user", "user_id"), Index("uq_push_subscriptions_endpoint", "endpoint", unique=True))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)              # the push service's address for this device
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)         # the device's public key (base64url)
    auth: Mapped[str] = mapped_column(String(100), nullable=False)           # the device's auth secret (base64url)
    device: Mapped[str | None] = mapped_column(String(120), nullable=True)   # e.g. "Chrome on Android", for the person's own list
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")