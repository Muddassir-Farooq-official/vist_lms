from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class ProfileChangeRequest(UUIDPKMixin, TimestampMixin, Base):
    """A student's or employee's own request to change their contact details and / or photo.
    Nothing reaches their record until an administrator approves it (or it is rejected with a reason).
    At most one PENDING request per person: a new change is added to it."""

    __tablename__ = "profile_change_requests"
    __table_args__ = (
        Index("ix_profile_change_requests_status", "status"),
        Index("ix_profile_change_requests_subject", "subject_type", "subject_id"),
    )

    subject_type: Mapped[str] = mapped_column(String(10), nullable=False)          # STUDENT | EMPLOYEE
    subject_id: Mapped[uuid.UUID] = mapped_column(nullable=False)                  # students.id / employees.id
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    # {"contact_no": {"old": "0300...", "new": "0301..."}, ...}
    changes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    photo_storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)     # the new photo, kept apart until approved
    photo_original_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    photo_check: Mapped[dict | None] = mapped_column(JSONB, nullable=True)                 # what the automatic face check found
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)                   # the person's own note to the office
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDING")     # PENDING APPROVED REJECTED CANCELLED
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
