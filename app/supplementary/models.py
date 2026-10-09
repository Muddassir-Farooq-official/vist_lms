"""Supplementary (reappear) attempts: a student who failed a course on marks sits one more paper
that replaces the mark of one component (normally the final exam)."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SupplementaryStatus(str, enum.Enum):
    REGISTERED = "REGISTERED"   # will sit the paper; no mark yet
    MARKED = "MARKED"           # mark entered (or marked absent); not yet published
    PUBLISHED = "PUBLISHED"     # the result is final and the student can see it


class SupplementaryAttempt(Base):
    __tablename__ = "supplementary_attempts"
    __table_args__ = (UniqueConstraint("enrollment_id", name="uq_supplementary_attempts_enrollment"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    enrollment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("enrollments.id", ondelete="CASCADE"), nullable=False)
    offering_assessment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("offering_assessments.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[SupplementaryStatus] = mapped_column(Enum(SupplementaryStatus, name="supplementary_status"), nullable=False,
                                                        default=SupplementaryStatus.REGISTERED)
    obtained_marks: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    absent: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    original_result: Mapped[dict] = mapped_column(JSON, nullable=False)       # the failed result, kept for the record
    new_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)      # what publishing produced
    registered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    marked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)