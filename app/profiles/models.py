from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class ProfilePartyType(str, enum.Enum):
    STUDENT = "STUDENT"
    EMPLOYEE = "EMPLOYEE"


class Qualification(UUIDPKMixin, TimestampMixin, Base):
    """One academic qualification entry (Matriculation, Bachelor's,
    Master's, ...) — a person can have several, so this is its own
    table rather than fixed columns on Student/Employee. The combined
    certificate PDF (all degrees/certificates in one file) is a
    separate field directly on Student/Employee, since it's one
    supporting document, not one per qualification, per approved
    decision.

    Shared between STUDENT and EMPLOYEE (same polymorphic party_type/
    party_id shape used by Fines and Complaints) rather than two
    near-identical tables — a qualification record looks the same
    regardless of whose it is."""

    __tablename__ = "qualifications"
    __table_args__ = (Index("ix_qualifications_party", "party_type", "party_id"),)

    party_type: Mapped[ProfilePartyType] = mapped_column(Enum(ProfilePartyType, name="profile_party_type"), nullable=False)
    party_id: Mapped[uuid.UUID] = mapped_column(nullable=False)

    degree_title: Mapped[str] = mapped_column(String(150), nullable=False)  # e.g. "BSN", "Matriculation", "PhD Nursing"
    institution_name: Mapped[str] = mapped_column(String(200), nullable=False)
    year_completed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Free text, not a number — grading systems vary (percentage, GPA,
    # letter grade, "Distinction") and forcing one format would lose
    # information for whichever system doesn't match.
    marks_or_grade: Mapped[str | None] = mapped_column(String(50), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)