from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class PayerType(str, enum.Enum):
    """Deliberately independent from library.models.BorrowerType — the
    Fee module must not import from the Library module (that coupling is
    exactly what got removed). Same concept, kept as two separate
    vocabularies on purpose."""

    STUDENT = "STUDENT"
    EMPLOYEE = "EMPLOYEE"


class FeeStructureStatus(str, enum.Enum):
    DRAFT = "DRAFT"  # admin is still configuring it — no charges generated yet
    ACTIVE = "ACTIVE"  # activated — charges have been auto-generated for every matching person
    CLOSED = "CLOSED"  # term over, no longer accepting new assignments (existing dues remain payable)


class AssignmentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    WAIVED = "WAIVED"  # admin excused this specific person from this specific charge
    CANCELLED = "CANCELLED"


class LedgerEntryType(str, enum.Enum):
    DEBIT = "DEBIT"  # a charge — one row per installment
    CREDIT = "CREDIT"  # a payment recorded against the assignment
    CONCESSION = "CONCESSION"  # a scholarship/discount taken off one installment (reduces what is owed, is not a payment)


class ScholarshipKind(str, enum.Enum):
    PERCENT = "PERCENT"  # value is a percentage (0-100] of each installment
    FIXED = "FIXED"  # value is a flat amount spread across the unpaid installments


class ScholarshipStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ENDED = "ENDED"  # stops applying to NEW charges; what was already given stays


class FeeType(UUIDPKMixin, TimestampMixin, Base):
    """Admin-managed catalog — Exam Fee, Clinical Fee, Library Fee, Tuition
    Fee, or anything else the institute wants to charge for. This is the
    category; FeeStructure is the actual configured charge (amount,
    term, schedule) within a category."""

    __tablename__ = "fee_types"
    __table_args__ = (UniqueConstraint("code", name="uq_fee_types_code"),)

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)


class FeeStructure(UUIDPKMixin, TimestampMixin, Base):
    """The actual configured charge: which fee type, which term, how much,
    how it's split into installments, the late-fee rate, and which
    program(s) it applies to (via fee_structure_programs — empty means
    institute-wide, per the approved design). Activating it (DRAFT ->
    ACTIVE) is what triggers auto-generating a FeeAssignment + full
    installment schedule for every currently-matching active student
    and/or employee, per the approved 'auto-apply' decision."""

    __tablename__ = "fee_structures"
    __table_args__ = (
        CheckConstraint("total_amount > 0", name="positive_total_amount"),
        CheckConstraint("installment_count > 0", name="positive_installment_count"),
        CheckConstraint("installment_interval_months > 0", name="positive_installment_interval"),
        CheckConstraint("late_fee_per_day IS NULL OR late_fee_per_day >= 0", name="non_negative_late_fee"),
    )

    fee_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fee_types.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)  # e.g. "Clinical Fee — Fall 2026 (BSN)"
    term_label: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "Fall 2026"
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    # Collection schedule. installment_count=1 means pay-in-full.
    # installment_interval_months controls spacing: 1 = monthly,
    # 2 = every other month (e.g. "3 times a semester" over a 6-month
    # term), etc. — one flexible knob instead of guessing a fixed cadence.
    installment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    installment_interval_months: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)  # first installment's due date

    # Late fee — a flat amount charged PER DAY an installment remains
    # unpaid past its due date (e.g. 200). NULL/0 means no late fee for
    # this fee structure.
    late_fee_per_day: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)

    applies_to_students: Mapped[bool] = mapped_column(default=True, nullable=False)
    applies_to_employees: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Subject/course-level scope (approved decision) — mutually exclusive
    # with the program/semester scoping above (see FeeStructureService
    # validation): a fee is EITHER program/semester-scoped (matches by
    # who the student IS, assigned once at activation or advancement)
    # OR course-scoped (matches by enrollment EVENTS, assigned fresh on
    # every enrollment including retakes — see
    # FeeStructureService.assign_course_fee_for_enrollment). Never both,
    # to keep the matching semantics unambiguous.
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[FeeStructureStatus] = mapped_column(
        Enum(FeeStructureStatus, name="fee_structure_status"), default=FeeStructureStatus.DRAFT, nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    fee_type: Mapped[FeeType] = relationship()
    programs: Mapped[list["FeeStructureProgram"]] = relationship(
        back_populates="fee_structure", cascade="all, delete-orphan"
    )
    semesters: Mapped[list["FeeStructureSemester"]] = relationship(
        back_populates="fee_structure", cascade="all, delete-orphan"
    )
    assignments: Mapped[list["FeeAssignment"]] = relationship(
        back_populates="fee_structure", cascade="all, delete-orphan"
    )


class FeeStructureProgram(Base):
    """Join row: which program(s) a fee structure is scoped to. No rows at
    all for a given fee_structure_id means institute-wide (applies to
    every active student regardless of program) — this is a deliberate
    'absence means unrestricted' design, not a bug: a fee structure with
    zero linked programs is not accidentally 'applies to nobody'."""

    __tablename__ = "fee_structure_programs"
    # NOTE: no separate UniqueConstraint needed here — the composite
    # primary key below already enforces this exact uniqueness. Adding one
    # on the same column pair would be redundant and, as caught by
    # `alembic check`, never actually gets created (Postgres won't make a
    # second identical constraint), which just produces permanent
    # model/migration drift for no functional benefit.

    fee_structure_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fee_structures.id", ondelete="CASCADE"), primary_key=True
    )
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), primary_key=True)

    fee_structure: Mapped[FeeStructure] = relationship(back_populates="programs")


class FeeStructureSemester(Base):
    """Join row: which semester number(s) within the program(s) above a
    fee structure is scoped to — e.g. 'Clinical Fee, BSN, semester 3
    only'. Same 'no rows means unrestricted' semantics as
    FeeStructureProgram: an empty set means every semester, not
    'applies to nobody'. Independent of program scoping — combining both
    (specific program + specific semester) is exactly what makes
    'BSN semester 3' expressible as a single fee structure."""

    __tablename__ = "fee_structure_semesters"

    fee_structure_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fee_structures.id", ondelete="CASCADE"), primary_key=True
    )
    semester_number: Mapped[int] = mapped_column(Integer, primary_key=True)

    fee_structure: Mapped[FeeStructure] = relationship(back_populates="semesters")


class FeeAssignment(UUIDPKMixin, TimestampMixin, Base):
    """One person's obligation under a fee structure — auto-created for
    every matching student/employee when the structure is activated.
    Polymorphic payer (STUDENT or EMPLOYEE), same pattern used
    throughout this system for anything that can apply to either.

    Uniqueness is TWO partial indexes, not one plain constraint — a
    course-scoped fee (approved decision: charged on every enrollment
    attempt, including retakes) needs a fresh assignment per
    enrollment_id, while every other fee still allows only one
    assignment per (structure, payer) ever. See migration
    c7e3f9a52d6b for the exact index definitions; the constraint here
    is descriptive only (this project hand-writes migrations rather
    than relying on autogenerate, so this doesn't drive DB DDL)."""

    __tablename__ = "fee_assignments"
    __table_args__ = (
        Index(
            "uq_fee_assignments_structure_payer_no_enrollment", "fee_structure_id", "payer_type", "payer_id",
            unique=True, postgresql_where=text("enrollment_id IS NULL"),
        ),
        Index(
            "uq_fee_assignments_structure_payer_enrollment", "fee_structure_id", "payer_type", "payer_id",
            "enrollment_id", unique=True, postgresql_where=text("enrollment_id IS NOT NULL"),
        ),
        Index("ix_fee_assignments_payer", "payer_type", "payer_id"),
    )

    fee_structure_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fee_structures.id", ondelete="CASCADE"), nullable=False
    )
    payer_type: Mapped[PayerType] = mapped_column(Enum(PayerType, name="fee_payer_type"), nullable=False)
    payer_id: Mapped[uuid.UUID] = mapped_column(nullable=False)  # students.id or employees.id
    # Set only for course-scoped fees — which specific enrollment (first
    # attempt or a later retake) this particular charge is for. Null for
    # every ordinary program/semester-scoped fee.
    enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("enrollments.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[AssignmentStatus] = mapped_column(
        Enum(AssignmentStatus, name="fee_assignment_status"), default=AssignmentStatus.ACTIVE, nullable=False
    )

    fee_structure: Mapped[FeeStructure] = relationship(back_populates="assignments")
    ledger_entries: Mapped[list["FeeLedgerEntry"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )


class FeeLedgerEntry(UUIDPKMixin, Base):
    """Append-only — a DEBIT per installment (auto-generated at
    activation), a CREDIT per payment recorded later. 'What's owed' and
    'what's overdue' are always DERIVED from these, never a cached
    balance — same discipline as every other financial ledger in this
    system (salary history, the removed library subscription ledger)."""

    __tablename__ = "fee_ledger_entries"
    __table_args__ = (
        Index("ix_fee_ledger_entries_assignment_id", "fee_assignment_id"),
        CheckConstraint("amount > 0", name="positive_amount"),
    )

    fee_assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fee_assignments.id", ondelete="CASCADE"), nullable=False
    )
    entry_type: Mapped[LedgerEntryType] = mapped_column(Enum(LedgerEntryType, name="fee_ledger_entry_type"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    installment_number: Mapped[int | None] = mapped_column(Integer, nullable=True)  # DEBIT rows only
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # DEBIT rows only
    # DEBIT rows only — how much of this specific installment's
    # calculated late fee has actually been posted as a real, collectible
    # Fine (via the Fines module) so far. The late fee itself keeps
    # growing daily while unpaid (see FeeStatusCalculator), but this only
    # advances when someone explicitly posts it — see
    # FeeAssignmentService.post_late_fee. Fixes a real gap: before this,
    # the calculated late fee was informational only and could never
    # actually be charged, paid, or waived.
    late_fee_charged_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, server_default="0")
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # CONCESSION rows only — the DEBIT installment this concession reduces, and the scholarship that granted it.
    related_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fee_ledger_entries.id", ondelete="CASCADE"), nullable=True
    )
    scholarship_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scholarships.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    assignment: Mapped[FeeAssignment] = relationship(back_populates="ledger_entries")


class Scholarship(UUIDPKMixin, TimestampMixin, Base):
    """A discount granted to one student (percentage or fixed amount),
    optionally limited to one fee structure. It never rewrites history:
    applying it adds CONCESSION rows to the fee ledger. See migration
    e4b9a7c3d261 for the exact DDL."""

    __tablename__ = "scholarships"
    __table_args__ = (
        CheckConstraint("value > 0", name="positive_scholarship_value"),
        Index("ix_scholarships_student", "student_id"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[ScholarshipKind] = mapped_column(Enum(ScholarshipKind, name="scholarship_kind"), nullable=False)
    value: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    fee_structure_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fee_structures.id", ondelete="SET NULL"), nullable=True
    )  # None = applies to every fee
    auto_apply: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[ScholarshipStatus] = mapped_column(
        Enum(ScholarshipStatus, name="scholarship_status"), default=ScholarshipStatus.ACTIVE, nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)