"""Official announcements: who issued them, who they are for, attachments, and who has read them."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Enum, ForeignKey, Index, String, Text, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class AnnouncementCategory(str, enum.Enum):
    GENERAL = "GENERAL"
    ACADEMIC = "ACADEMIC"
    EXAMINATION = "EXAMINATION"
    ADMISSIONS = "ADMISSIONS"
    FEES = "FEES"
    EVENT = "EVENT"
    HOLIDAY = "HOLIDAY"
    SAFETY = "SAFETY"


CATEGORY_LABELS = {
    AnnouncementCategory.GENERAL: "General", AnnouncementCategory.ACADEMIC: "Academic", AnnouncementCategory.EXAMINATION: "Examinations",
    AnnouncementCategory.ADMISSIONS: "Admissions", AnnouncementCategory.FEES: "Fees", AnnouncementCategory.EVENT: "Events",
    AnnouncementCategory.HOLIDAY: "Holidays", AnnouncementCategory.SAFETY: "Safety & security",
}


class AnnouncementPriority(str, enum.Enum):
    NORMAL = "NORMAL"
    IMPORTANT = "IMPORTANT"
    URGENT = "URGENT"


class AnnouncementStatus(str, enum.Enum):
    DRAFT = "DRAFT"            # being written; nobody else can see it
    PUBLISHED = "PUBLISHED"    # live for its audience
    ARCHIVED = "ARCHIVED"      # withdrawn from view (kept for the record)


class IssuingAuthority(UUIDPKMixin, TimestampMixin, Base):
    """The official an announcement is issued by -- e.g. "Dr. A. Khan, Managing Director". Announcements keep their own
    copy of the name and designation, so renaming or removing someone here never rewrites past announcements."""

    __tablename__ = "issuing_authorities"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    designation: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())


class Announcement(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "announcements"
    __table_args__ = (Index("ix_announcements_status", "status"), Index("ix_announcements_submitted_by", "submitted_by"))

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[AnnouncementCategory] = mapped_column(
        Enum(AnnouncementCategory, name="announcement_category"), nullable=False, default=AnnouncementCategory.GENERAL
    )
    priority: Mapped[AnnouncementPriority] = mapped_column(
        Enum(AnnouncementPriority, name="announcement_priority"), nullable=False, default=AnnouncementPriority.NORMAL
    )

    # Issued by (a snapshot, see IssuingAuthority)
    issuer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("issuing_authorities.id", ondelete="SET NULL"), nullable=True)
    issuer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    issuer_designation: Mapped[str] = mapped_column(String(120), nullable=False)

    # Who it is for
    audience_students: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    audience_teachers: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    audience_staff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())  # librarian, accounts...
    # Optional: only students of these programs (empty / null = every student)
    program_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Optional: only students in these semesters (with or without a program filter) -- migration b9d1f3a5c7e9
    semesters: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Optional: only students enrolled in these course sections (course offerings). When set, programs/semesters are ignored.
    offering_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Written by a teacher or staff member and waiting for the admin: review = PENDING -> APPROVED (published) or REJECTED
    # (they can fix it and send it again) or CANCELLED (they took it back). NULL = written by the admin. While not approved the
    # announcement stays a DRAFT, so nobody else sees it. It is issued in the writer's own name and job (issuer_* below).
    review: Mapped[str | None] = mapped_column(String(10), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[AnnouncementStatus] = mapped_column(
        Enum(AnnouncementStatus, name="announcement_status"), nullable=False, default=AnnouncementStatus.DRAFT
    )
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AnnouncementAttachment(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "announcement_attachments"
    __table_args__ = (Index("ix_announcement_attachments_announcement", "announcement_id"),)

    announcement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AnnouncementRead(Base):
    """One row per person who has opened an announcement (drives the unread badge and the 'seen by' figure)."""

    __tablename__ = "announcement_reads"

    announcement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("announcements.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)