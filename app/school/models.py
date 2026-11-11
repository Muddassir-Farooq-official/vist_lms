"""School mode: sections of a class in a session, and the subjects a class studies.

A CLASS is a Program with school_level set (app/programs/models.py), e.g. "Class 5" or "Class 9 (Science)".
A SESSION is an academic term (app/academics/terms.py), e.g. "2026-27".
A SUBJECT is a Course linked to the class (CourseProgram, semester 1) and listed here in ClassSubject.
A SECTION (Class 5 - A) holds the class teacher; each subject is taught in each section as a CourseOffering
(term = session, section_label = the section), which carries the subject teacher.
A student belongs to a section through Student.program_id + Student.section_label, and is enrolled in the
section's subjects.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ClassSubject(Base):
    """A subject of a class, in report-card order. Optional subjects (e.g. A Level choices) are chosen per student."""
    __tablename__ = "class_subjects"

    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), primary_key=True)
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_optional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SchoolSection(Base):
    __tablename__ = "school_sections"
    __table_args__ = (UniqueConstraint("term_id", "program_id", "label", name="uq_school_sections_term_program_label"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    term_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("academic_terms.id", ondelete="CASCADE"), nullable=False, index=True)
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(30), nullable=False)
    class_teacher_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True)
    room: Mapped[str | None] = mapped_column(String(50), nullable=True)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
