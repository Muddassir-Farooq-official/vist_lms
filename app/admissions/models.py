"""Admissions: a round per program and term (seats, merit formula), and the applications in it."""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RoundStatus(str, enum.Enum):
    OPEN = "OPEN"        # taking applications, verifying, publishing merit lists
    CLOSED = "CLOSED"    # admissions for this program and term are over


class ApplicantStatus(str, enum.Enum):
    SUBMITTED = "SUBMITTED"      # entered; documents not checked yet
    VERIFIED = "VERIFIED"        # documents checked: on the merit list
    REJECTED = "REJECTED"        # not eligible / documents not genuine (reason kept)
    OFFERED = "OFFERED"          # selected in a merit list; must pay and be admitted before the deadline
    ADMITTED = "ADMITTED"        # now a student (student_id)
    EXPIRED = "EXPIRED"          # offer not taken up in time; seat goes to the next merit list
    WITHDRAWN = "WITHDRAWN"      # the applicant withdrew


class AdmissionRound(Base):
    __tablename__ = "admission_rounds"
    __table_args__ = (UniqueConstraint("term_label", "program_id", name="uq_admission_rounds_term_program"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    term_label: Mapped[str] = mapped_column(String(50), nullable=False)
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), nullable=False)
    seats: Mapped[int] = mapped_column(Integer, nullable=False)
    weight_matric: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)   # % of the aggregate
    weight_inter: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    weight_test: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    min_inter_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=45, server_default="45")
    closes_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[RoundStatus] = mapped_column(Enum(RoundStatus, name="admission_round_status"), nullable=False, default=RoundStatus.OPEN)
    merit_lists_published: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Applicant(Base):
    __tablename__ = "applicants"
    __table_args__ = (UniqueConstraint("round_id", "cnic", name="uq_applicants_round_cnic"),
                      UniqueConstraint("application_no", name="uq_applicants_application_no"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    round_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("admission_rounds.id", ondelete="CASCADE"), nullable=False, index=True)
    application_no: Mapped[str] = mapped_column(String(30), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    cnic: Mapped[str] = mapped_column(String(15), nullable=False)
    contact_no: Mapped[str] = mapped_column(String(20), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False)          # everything the student record needs (validated on entry)
    matric_obtained: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False)
    matric_total: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False)
    inter_obtained: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False)
    inter_total: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False)
    test_obtained: Mapped[float | None] = mapped_column(Numeric(7, 2), nullable=True)
    test_total: Mapped[float | None] = mapped_column(Numeric(7, 2), nullable=True)
    aggregate: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    status: Mapped[ApplicantStatus] = mapped_column(Enum(ApplicantStatus, name="applicant_status"), nullable=False, default=ApplicantStatus.SUBMITTED)
    merit_list_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    merit_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offer_expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(300), nullable=True)
    student_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())