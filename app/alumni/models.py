"""Graduation lists, degree and transcript issue tracking, and alumni (their details, the directory and surveys).

A graduation list is drawn up for a program: every current student with their degree audit and clearance. The office
ticks who graduates and confirms; each ticked student is marked graduated (the usual checks still apply) and keeps
their login as an alumnus: their student menu is replaced by the alumni portal (their details, degree and transcript,
the directory, surveys)."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class GraduationList(Base):
    __tablename__ = "graduation_lists"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(120), nullable=False)                 # "BSN graduates, Convocation 2026"
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="RESTRICT"), nullable=False)
    graduation_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="DRAFT")     # DRAFT | CONFIRMED
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GraduationEntry(Base):
    __tablename__ = "graduation_entries"
    __table_args__ = (UniqueConstraint("list_id", "student_id", name="uq_graduation_entries_once"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    list_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("graduation_lists.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    cgpa: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)
    credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    problems: Mapped[list | None] = mapped_column(JSON, nullable=True)                # degree audit + clearance, when drawn up
    included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result: Mapped[str | None] = mapped_column(String(10), nullable=True)            # GRADUATED | SKIPPED (on confirming)
    result_note: Mapped[str | None] = mapped_column(String(300), nullable=True)


class DocumentIssue(Base):
    """A degree or transcript being prepared for a graduate, through to being collected or posted."""

    __tablename__ = "document_issues"
    __table_args__ = (Index("ix_document_issues_student", "student_id"), Index("ix_document_issues_status", "status"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)                    # DEGREE | TRANSCRIPT
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="REQUESTED")
    # REQUESTED -> PREPARING -> READY -> COLLECTED | POSTED   (or CANCELLED)
    serial_no: Mapped[str | None] = mapped_column(String(40), nullable=True)         # the degree's or transcript's number
    copies: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    requested_by_student: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    handed_to: Mapped[str | None] = mapped_column(String(120), nullable=True)        # who collected it, or the courier and tracking number
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    history: Mapped[list | None] = mapped_column(JSON, nullable=True)                # [{status, at, by}]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AlumniProfile(Base):
    __tablename__ = "alumni_profiles"

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    employer: Mapped[str | None] = mapped_column(String(150), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)
    further_study: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(150), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    linkedin: Mapped[str | None] = mapped_column(String(200), nullable=True)
    in_directory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)        # other alumni see name, program, year, work, city
    share_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)       # ... and email / phone / LinkedIn
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AlumniSurvey(Base):
    __tablename__ = "alumni_surveys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    intro: Mapped[str | None] = mapped_column(Text, nullable=True)
    questions: Mapped[list] = mapped_column(JSON, nullable=False)                    # [{id, text, kind: RATING|CHOICE|TEXT, choices?}]
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AlumniSurveyResponse(Base):
    __tablename__ = "alumni_survey_responses"
    __table_args__ = (UniqueConstraint("survey_id", "student_id", name="uq_alumni_survey_responses_once"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    survey_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alumni_surveys.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    answers: Mapped[dict] = mapped_column(JSON, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
