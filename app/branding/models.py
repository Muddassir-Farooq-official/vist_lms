"""The institute's identity, one row: name, contact details, logos, colour, bank details, signatory, document footer.

Every page, PDF, report and SMS reads these through app.core.brand (copied into `settings` on each worker by
app.branding.service.apply_from_db), so one change in Settings -> Institute shows everywhere.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class InstituteProfile(Base):
    __tablename__ = "institute_profile"

    id: Mapped[str] = mapped_column(String(10), primary_key=True, default="main")      # always "main": one institute per installation
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    short_name: Mapped[str] = mapped_column(String(40), nullable=False)
    tagline: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(60), nullable=True)
    email: Mapped[str | None] = mapped_column(String(150), nullable=True)
    website: Mapped[str | None] = mapped_column(String(150), nullable=True)
    registration_no: Mapped[str | None] = mapped_column(String(120), nullable=True)
    primary_color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    document_footer: Mapped[str | None] = mapped_column(String(300), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    bank_account_title: Mapped[str | None] = mapped_column(String(150), nullable=True)
    bank_account_no: Mapped[str | None] = mapped_column(String(60), nullable=True)
    bank_branch: Mapped[str | None] = mapped_column(String(150), nullable=True)
    signatory_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    signatory_title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    logo_key: Mapped[str | None] = mapped_column(String(255), nullable=True)           # files under var/storage/branding/
    logo_dark_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    favicon_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signature_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
