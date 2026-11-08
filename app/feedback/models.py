"""Course & teacher feedback from students (migration b3d5f7a9c1e2).

Anonymous by design: the answers (feedback_answers, feedback_comments) carry NO student and NO time, and their ids are
random, so they cannot be traced back to who gave them or matched by when. feedback_submissions records only THAT a
student answered for a course (to stop answering twice and to show response rates), never what they answered.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, Integer, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDPKMixin


class FeedbackQuestion(UUIDPKMixin, Base):
    __tablename__ = "feedback_questions"

    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    # Changing a question's wording would change the meaning of answers already given, so an edited question is a new
    # row and the old one is switched off (kept for the old answers).
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class FeedbackSubmission(Base):
    __tablename__ = "feedback_submissions"

    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    course_offering_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("course_offerings.id", ondelete="CASCADE"), primary_key=True)
    submitted_on: Mapped[date] = mapped_column(Date(), nullable=False)


class FeedbackAnswer(UUIDPKMixin, Base):
    __tablename__ = "feedback_answers"
    __table_args__ = (
        CheckConstraint("score BETWEEN 1 AND 5", name="score_1_to_5"),
        Index("ix_feedback_answers_offering", "course_offering_id"),
    )

    course_offering_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feedback_questions.id", ondelete="CASCADE"), nullable=False)
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class FeedbackComment(UUIDPKMixin, Base):
    __tablename__ = "feedback_comments"
    __table_args__ = (Index("ix_feedback_comments_offering", "course_offering_id"),)

    course_offering_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
