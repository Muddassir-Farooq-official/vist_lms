"""The gate: a visitor log book. Who came, when, why, whom they met, and when they left.

Students and staff are not checked in here (staff arrival / leaving is recorded in Staff attendance)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Identity, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class VisitorEntry(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "visitor_entries"
    __table_args__ = (Index("ix_visitor_entries_entry_at", "entry_at"), Index("ix_visitor_entries_cnic", "cnic"))

    serial_no: Mapped[int] = mapped_column(Integer, Identity(), unique=True, nullable=False)        # VIS-000123
    visitor_name: Mapped[str] = mapped_column(String(150), nullable=False)
    cnic: Mapped[str | None] = mapped_column(String(15), nullable=True)                              # 13 digits, no dashes
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    coming_from: Mapped[str | None] = mapped_column(String(150), nullable=True)                      # address / organisation
    purpose: Mapped[str] = mapped_column(String(200), nullable=False)
    person_to_meet: Mapped[str | None] = mapped_column(String(150), nullable=True)
    vehicle_no: Mapped[str | None] = mapped_column(String(30), nullable=True)
    people_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    card_no: Mapped[str | None] = mapped_column(String(20), nullable=True)                           # visitor badge handed over
    entry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    in_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    out_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)