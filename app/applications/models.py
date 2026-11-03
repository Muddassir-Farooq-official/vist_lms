"""Applications: a student or employee asks the administration for something (leave, a certificate, a concession...) and
follows it through to a decision."""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Enum, ForeignKey, Identity, Index, Integer, String, Text, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class ApplicationStatus(str, enum.Enum):
    SUBMITTED = "SUBMITTED"                  # waiting for the administration
    UNDER_REVIEW = "UNDER_REVIEW"            # the administration has started looking at it
    MORE_INFO_NEEDED = "MORE_INFO_NEEDED"    # sent back to the applicant with a question
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"                  # taken back by the applicant


OPEN_STATUSES = (ApplicationStatus.SUBMITTED, ApplicationStatus.UNDER_REVIEW, ApplicationStatus.MORE_INFO_NEEDED)


class ApplicantKind(str, enum.Enum):
    STUDENT = "STUDENT"
    EMPLOYEE = "EMPLOYEE"


class ApplicationType(UUIDPKMixin, TimestampMixin, Base):
    """What can be applied for. The administrator manages this list."""

    __tablename__ = "application_types"
    __table_args__ = (UniqueConstraint("name", name="uq_application_types_name"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)        # shown to the applicant: what to include, who decides
    for_students: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    for_employees: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    requires_dates: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    requires_attachment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    yearly_limit_days: Mapped[int | None] = mapped_column(Integer, nullable=True)   # e.g. 10 days of casual leave a year (informational)
    # Approving this kind of application (with dates) excuses those days: staff attendance is marked
    # "excused" (no deduction) and a student's classes are marked LEAVE (not counted for or against).
    excuses_attendance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")


class Application(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "applications"
    __table_args__ = (
        Index("ix_applications_applicant", "applicant_user_id"),
        Index("ix_applications_status", "status"),
    )

    serial_no: Mapped[int] = mapped_column(Integer, Identity(), unique=True, nullable=False)       # APP-2026-00042
    type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("application_types.id", ondelete="RESTRICT"), nullable=False)
    applicant_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    applicant_kind: Mapped[ApplicantKind] = mapped_column(Enum(ApplicantKind, name="applicant_kind"), nullable=False)
    applicant_ref_id: Mapped[uuid.UUID] = mapped_column(nullable=False)                             # the student's / employee's own record

    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    from_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    to_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, name="application_status"), nullable=False, default=ApplicationStatus.SUBMITTED
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)   # when the administration first looked at it
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApplicationAttachment(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "application_attachments"
    __table_args__ = (Index("ix_application_attachments_application", "application_id"),)

    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class ApplicationComment(UUIDPKMixin, TimestampMixin, Base):
    """The conversation between the applicant and the administration."""

    __tablename__ = "application_comments"
    __table_args__ = (Index("ix_application_comments_application", "application_id"),)

    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    side: Mapped[str] = mapped_column(String(10), nullable=False)       # APPLICANT or ADMIN
    body: Mapped[str] = mapped_column(Text, nullable=False)

class ApplicationForward(UUIDPKMixin, TimestampMixin, Base):
    """The administration hands an application to a department (Exams, Accounts, Library ...) to act on.

    The department marks it In progress, then Done or Cannot do with a note, which sends it back to the
    administration; the administration still makes the final decision. The applicant follows the stage
    ("Forwarded to Exams", "Exams is working on it", "Back with the administration") but not the internal notes.
    """

    __tablename__ = "application_forwards"
    __table_args__ = (
        Index("ix_application_forwards_application", "application_id"),
        Index("ix_application_forwards_department_status", "department", "status"),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    department: Mapped[str] = mapped_column(String(30), nullable=False)          # a role code: EXAM_CONTROLLER, ACCOUNTS_OFFICER ...
    note: Mapped[str | None] = mapped_column(Text, nullable=True)               # the administration's instructions (internal)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="FORWARDED", server_default="FORWARDED")  # FORWARDED | IN_PROGRESS | DONE | CANNOT_DO | RECALLED
    forwarded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)           # the department's note back (internal)
    responded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)