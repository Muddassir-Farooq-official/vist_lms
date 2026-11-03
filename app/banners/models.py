"""Top bars: a strip across the top of every page (like the "Viewing as" bar) that the administration shows to chosen
people, e.g. "Register for the Google AI course by 30 Nov  [Register]". Each bar has a message, an optional button
that opens a link, a colour, an audience (everyone, roles, programs / semesters, or named people), an optional
schedule, and an on / off switch. Who clicked and who closed it is recorded per person.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin

TONES = ("blue", "green", "orange", "red", "purple", "teal", "ink", "yellow")


class TopBanner(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "top_banners"

    name: Mapped[str] = mapped_column(String(120), nullable=False)                 # for the administration only
    message: Mapped[str] = mapped_column(String(300), nullable=False)
    button_label: Mapped[str | None] = mapped_column(String(40), nullable=True)
    button_url: Mapped[str | None] = mapped_column(String(500), nullable=True)     # https://... or a page of this site (/...)
    new_tab: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    tone: Mapped[str] = mapped_column(String(10), nullable=False, default="blue", server_default="blue")
    # {"everyone": bool, "roles": [...], "program_ids": [...], "semesters": [...], "user_ids": [...]}; anyone matching ANY part sees it.
    audience: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    dismissible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class TopBannerSeen(Base):
    """One row per person who clicked the button or closed the bar."""
    __tablename__ = "top_banner_seen"

    banner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("top_banners.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)