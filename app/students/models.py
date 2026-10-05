from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPKMixin, utcnow


class StudentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    GRADUATED = "GRADUATED"
    DROPPED = "DROPPED"


class Student(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "students"
    __table_args__ = (
        UniqueConstraint("cnic", name="uq_students_cnic"),
        UniqueConstraint("roll_number", name="uq_students_roll_number"),  # globally unique, per approval
        UniqueConstraint("user_id", name="uq_students_user_id"),
        Index("ix_students_program_id", "program_id"),
        Index("ix_students_status", "status"),
        # Real GIN trigram indexes (requires pg_trgm — created explicitly in
        # migration 221008dd59b1). Declared here too so `alembic check`
        # doesn't flag them as drift.
        Index(
            "ix_students_first_name_trgm", "first_name", postgresql_using="gin", postgresql_ops={"first_name": "gin_trgm_ops"}
        ),
        Index(
            "ix_students_last_name_trgm", "last_name", postgresql_using="gin", postgresql_ops={"last_name": "gin_trgm_ops"}
        ),
        CheckConstraint(r"cnic ~ '^[0-9]{5}-[0-9]{7}-[0-9]{1}$'", name="ck_students_cnic_format"),
        CheckConstraint("current_semester IS NULL OR current_semester >= 1", name="current_semester_positive"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    program_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("programs.id", ondelete="RESTRICT"), nullable=True
    )
    source_import_job_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    gender: Mapped[str] = mapped_column(String(20), nullable=False)
    cnic: Mapped[str] = mapped_column(String(15), nullable=False)  # mandatory per approved decision
    contact_no: Mapped[str] = mapped_column(String(20), nullable=False)
    marital_status: Mapped[str] = mapped_column(String(20), nullable=False)
    religion: Mapped[str] = mapped_column(String(50), nullable=False)
    nationality: Mapped[str] = mapped_column(String(50), nullable=False)

    # System-generated (BSN-2026-0001, ...), never admin-supplied — see
    # app/core/sequences.py. Doubles as the student's permanent login
    # username, so it's immutable once created (never editable via
    # StudentUpdateRequest).
    roll_number: Mapped[str] = mapped_column(String(30), nullable=False)
    admission_fee_paid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    enrollment_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Added for real-institute launch: contact/medical basics and how the
    # student was admitted. All nullable so existing rows stay valid.
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    blood_group: Mapped[str | None] = mapped_column(String(5), nullable=True)
    admission_session: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g. "Fall 2026"
    admission_category: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g. "Open Merit"
    # Cohort/section the student attends ("A", "B", "Morning"...). Drives which
    # section offerings they may enroll in and batch enrollment by section.
    section_label: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Profile photo and a single combined PDF of all academic
    # certificates/degrees — uploaded files, server-generated storage
    # keys (never the user's filename), see app/core/storage.py.
    # Individual qualification records (degree/institute/marks) live in
    # their own table (app/profiles/models.py) since a person can have
    # several; this PDF is the one supporting document for all of them
    # combined, per approved decision.
    photo_storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    photo_original_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    certificate_storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    certificate_original_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Which semester (1, 2, 3...) of their program the student is
    # currently in — distinct from term_label ("Fall 2026") used
    # elsewhere, which is a calendar period shared across all programs.
    # Nullable: existing students predate this field and aren't forced to
    # backfill it. Used by Fee Structures to scope charges to specific
    # semesters within a program (e.g. "Clinical Fee, BSN, semester 3 only").
    current_semester: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[StudentStatus] = mapped_column(
        Enum(StudentStatus, name="student_status"), default=StudentStatus.ACTIVE, nullable=False
    )
    is_active_flag: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    guardians: Mapped[list["StudentGuardian"]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )
    emergency_contacts: Mapped[list["StudentEmergencyContact"]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )
    addresses: Mapped[list["StudentAddress"]] = relationship(
        back_populates="student", cascade="all, delete-orphan"
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def has_photo(self) -> bool:
        return self.photo_storage_key is not None

    @property
    def has_certificate(self) -> bool:
        return self.certificate_storage_key is not None


class StudentGuardian(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "student_guardians"
    __table_args__ = (
        Index("ix_student_guardians_student_id", "student_id"),
        CheckConstraint(r"cnic ~ '^[0-9]{5}-[0-9]{7}-[0-9]{1}$'", name="ck_student_guardians_cnic_format"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    cnic: Mapped[str] = mapped_column(String(15), nullable=False)
    contact_no: Mapped[str | None] = mapped_column(String(20), nullable=True)
    relation: Mapped[str] = mapped_column(String(50), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    student: Mapped[Student] = relationship(back_populates="guardians")


class StudentEmergencyContact(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "student_emergency_contacts"
    __table_args__ = (Index("ix_student_emergency_contacts_student_id", "student_id"),)

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    contact_no: Mapped[str] = mapped_column(String(20), nullable=False)
    relation: Mapped[str | None] = mapped_column(String(50), nullable=True)

    student: Mapped[Student] = relationship(back_populates="emergency_contacts")


class StudentAddress(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "student_addresses"
    __table_args__ = (Index("ix_student_addresses_student_id", "student_id"),)

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    address_type: Mapped[str] = mapped_column(String(20), default="PRESENT", nullable=False)
    address_line: Mapped[str] = mapped_column(String(255), nullable=False)
    province: Mapped[str] = mapped_column(String(100), nullable=False)
    district: Mapped[str] = mapped_column(String(100), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)

    student: Mapped[Student] = relationship(back_populates="addresses")


class StudentStatusHistory(UUIDPKMixin, Base):
    __tablename__ = "student_status_history"
    __table_args__ = (Index("ix_student_status_history_student_id", "student_id"),)

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    old_status: Mapped[StudentStatus | None] = mapped_column(
        Enum(StudentStatus, name="student_status"), nullable=True
    )
    new_status: Mapped[StudentStatus] = mapped_column(Enum(StudentStatus, name="student_status"), nullable=False)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)