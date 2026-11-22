"""Exam paper files: the teacher sends the question paper (PDF or Word) for their datesheet paper; the exam office
downloads and prints it, or returns it with a note. Every version is kept; students and parents never see them."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PaperFile(Base):
    __tablename__ = "exam_paper_files"
    __table_args__ = (Index("ix_exam_paper_files_paper", "exam_paper_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    exam_paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exam_papers.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)          # from the teacher: copies, special instructions
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="SENT")    # SENT | PRINTED | RETURNED | REPLACED
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    return_note: Mapped[str | None] = mapped_column(String(500), nullable=True)   # why the exam office sent it back
