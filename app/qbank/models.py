"""Question bank: teachers add questions to their courses (tagged with CLO, Bloom level, difficulty and marks); the exam
office builds papers from a blueprint (how many questions of which kind, level and difficulty), adjusts them, and makes
them final. Students never see the bank or a paper (no student endpoint exists)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BankQuestion(Base):
    __tablename__ = "qbank_questions"
    __table_args__ = (Index("ix_qbank_questions_course_kind", "course_id", "kind"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(6), nullable=False)                 # MCQ | SHORT | LONG
    text: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)           # MCQ: ["...", "...", ...]
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)             # MCQ: the right option's letter; else the marking key
    clo: Mapped[str | None] = mapped_column(String(12), nullable=True)          # CLO2
    bloom: Mapped[str | None] = mapped_column(String(4), nullable=True)         # C1..C6, P1..P7, A1..A5
    difficulty: Mapped[str] = mapped_column(String(6), nullable=False, default="MEDIUM")    # EASY | MEDIUM | HARD
    marks: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    times_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuestionPaper(Base):
    __tablename__ = "qbank_papers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(150), nullable=False)             # "Midterm, Fall 2026"
    exam_paper_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("exam_papers.id", ondelete="SET NULL"), nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instructions: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(6), nullable=False, default="DRAFT")    # DRAFT | FINAL
    blueprint: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finalised_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    finalised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaperItem(Base):
    __tablename__ = "qbank_paper_items"
    __table_args__ = (UniqueConstraint("paper_id", "question_id", name="uq_qbank_paper_items_once"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("qbank_papers.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("qbank_questions.id", ondelete="RESTRICT"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    marks: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
