from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class ComplaintPartyType(str, enum.Enum):
    STUDENT = "STUDENT"
    EMPLOYEE = "EMPLOYEE"


class ComplaintReason(str, enum.Enum):
    """Fixed, extensible category list — add new values here as new
    complaint types come up; nothing else needs to change. OTHER always
    stays as the catch-all with `custom_reason` capturing specifics."""

    HARASSMENT = "HARASSMENT"
    MISBEHAVIOR = "MISBEHAVIOR"
    DISCRIMINATION = "DISCRIMINATION"
    NEGLIGENCE = "NEGLIGENCE"
    ACADEMIC_DISPUTE = "ACADEMIC_DISPUTE"
    FACILITY_ISSUE = "FACILITY_ISSUE"
    FINANCIAL_DISPUTE = "FINANCIAL_DISPUTE"
    OTHER = "OTHER"


class ComplaintStatus(str, enum.Enum):
    SUBMITTED = "SUBMITTED"  # just filed, awaiting admin review
    UNDER_REVIEW = "UNDER_REVIEW"  # admin is actively looking into it
    RESOLVED = "RESOLVED"  # closed — either by admin or by the complainant themselves
    DISMISSED = "DISMISSED"  # admin closed it without action (invalid, unfounded, etc.)
    WITHDRAWN = "WITHDRAWN"  # complainant pulled it back themselves


class Complaint(UUIDPKMixin, TimestampMixin, Base):
    """A complaint/feedback record, complainant and target both
    polymorphic (STUDENT or EMPLOYEE) — the same 'which kind of person'
    shape already used by Fines and Library, kept as its own local type
    rather than a cross-module import (consistent with how those two
    modules independently define the same STUDENT/EMPLOYEE distinction
    rather than depending on each other; worth hoisting into a shared
    app.core type if this duplication ever becomes real pain, not
    urgent today).

    Scalable by design (approved decision): who is ALLOWED to file one
    is controlled entirely by the complaint.submit permission grant in
    app/auth/permissions.py — STUDENT and TEACHER hold it today, adding
    another role later (e.g. LIBRARIAN) is a one-line permission change,
    nothing here needs to change."""

    __tablename__ = "complaints"
    __table_args__ = (
        Index("ix_complaints_complainant", "complainant_type", "complainant_id"),
        Index("ix_complaints_target", "target_type", "target_id"),
        Index("ix_complaints_status", "status"),
    )

    complainant_type: Mapped[ComplaintPartyType] = mapped_column(
        Enum(ComplaintPartyType, name="complaint_party_type"), nullable=False
    )
    complainant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)  # references students.id or employees.id

    target_type: Mapped[ComplaintPartyType] = mapped_column(
        Enum(ComplaintPartyType, name="complaint_party_type"), nullable=False
    )
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)

    reason: Mapped[ComplaintReason] = mapped_column(Enum(ComplaintReason, name="complaint_reason"), nullable=False)
    custom_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)  # only meaningful when reason=OTHER
    description: Mapped[str] = mapped_column(String(2000), nullable=False)

    # Filed anonymously: the complainant's name is hidden on every screen except to the person who filed it;
    # an administrator can reveal it deliberately (and that is written to the audit log).
    is_anonymous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())

    status: Mapped[ComplaintStatus] = mapped_column(
        Enum(ComplaintStatus, name="complaint_status"), default=ComplaintStatus.SUBMITTED, nullable=False
    )
    under_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String(500), nullable=True)