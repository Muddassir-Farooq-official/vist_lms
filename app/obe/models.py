from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

BLOOM = ("C1", "C2", "C3", "C4", "C5", "C6", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "A1", "A2", "A3", "A4", "A5")
BLOOM_NAMES = {"C1": "Remember", "C2": "Understand", "C3": "Apply", "C4": "Analyse", "C5": "Evaluate", "C6": "Create",
               "P1": "Perception", "P2": "Set", "P3": "Guided response", "P4": "Mechanism", "P5": "Complex response", "P6": "Adaptation",
               "P7": "Origination", "A1": "Receiving", "A2": "Responding", "A3": "Valuing", "A4": "Organisation", "A5": "Characterisation"}


class ProgramOutcome(Base):
    """A program learning outcome (PLO), set by the administration per program."""
    __tablename__ = "obe_plos"
    __table_args__ = (UniqueConstraint("program_id", "code", name="uq_obe_plos_program_code"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(12), nullable=False)               # PLO1
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ObeProgramSetting(Base):
    """When a CLO / PLO counts as attained for one program (defaults: student 50%, batch 60% of students)."""
    __tablename__ = "obe_program_settings"

    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), primary_key=True)
    student_pct: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=50)
    batch_pct: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=60)


class CourseOutcome(Base):
    """A course learning outcome (CLO) of one class (course offering), written by its teacher."""
    __tablename__ = "obe_clos"
    __table_args__ = (UniqueConstraint("course_offering_id", "code", name="uq_obe_clos_offering_code"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    course_offering_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(12), nullable=False)                 # CLO1
    text: Mapped[str] = mapped_column(Text, nullable=False)
    bloom: Mapped[str] = mapped_column(String(4), nullable=False)                 # C1..C6, P1..P7, A1..A5
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CloPlo(Base):
    __tablename__ = "obe_clo_plos"

    clo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("obe_clos.id", ondelete="CASCADE"), primary_key=True)
    plo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("obe_plos.id", ondelete="CASCADE"), primary_key=True)


class CourseFile(Base):
    """The OBE state of one class: DRAFT (teacher edits) -> SUBMITTED -> LOCKED by the exam office (or sent back to DRAFT)."""
    __tablename__ = "obe_course_files"

    course_offering_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("course_offerings.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="DRAFT")
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class AssessmentItem(Base):
    """What an assessment measures. With a label it is a question part (its own max marks; marks entered per part);
    without, the whole assessment counts towards the CLO (share = max_marks of the assessment's total)."""
    __tablename__ = "obe_items"
    __table_args__ = (Index("ix_obe_items_assessment", "offering_assessment_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    offering_assessment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("offering_assessments.id", ondelete="CASCADE"), nullable=False)
    clo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("obe_clos.id", ondelete="CASCADE"), nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String(20), nullable=True)          # "Q1", "Q2(b)"; None = the whole assessment
    max_marks: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ItemMark(Base):
    """A student's marks in one question part (only parts with a label)."""
    __tablename__ = "obe_item_marks"

    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("obe_items.id", ondelete="CASCADE"), primary_key=True)
    enrollment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("enrollments.id", ondelete="CASCADE"), primary_key=True)
    obtained: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    entered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
