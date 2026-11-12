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

import enum
import uuid
from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Time, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
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


# ---------------------------------------------------------------- stage 2: daily use (app/school/daily.py)
class DayStatus(str, enum.Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    LATE = "LATE"
    LEAVE = "LEAVE"          # approved leave: counts neither for nor against


class DailyAttendance(Base):
    """The morning roll call: one mark per student per day, by the class teacher (or the cover teacher that day)."""
    __tablename__ = "school_daily_attendance"
    __table_args__ = (UniqueConstraint("student_id", "day", name="uq_school_daily_attendance_student_day"),
                      Index("ix_school_daily_attendance_section_day", "section_id", "day"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    section_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("school_sections.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[DayStatus] = mapped_column(Enum(DayStatus, name="school_day_status"), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    marked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    marked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    told_status: Mapped[str | None] = mapped_column(String(10), nullable=True)      # what the parents were last told (ABSENT / LATE)


class RollCallCover(Base):
    """A teacher who takes a section's roll call on one day, when the class teacher is away."""
    __tablename__ = "school_roll_call_cover"
    __table_args__ = (UniqueConstraint("section_id", "day", name="uq_school_roll_call_cover_section_day"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    section_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("school_sections.id", ondelete="CASCADE"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    set_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Homework(Base):
    """One subject's homework for a section on a day (the homework diary)."""
    __tablename__ = "school_homework"
    __table_args__ = (Index("ix_school_homework_section_day", "section_id", "set_on"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    section_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("school_sections.id", ondelete="CASCADE"), nullable=False)
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    set_on: Mapped[date] = mapped_column(Date, nullable=False)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    details: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    file_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PtmDay(Base):
    """A parent-teacher meeting day: each class teacher gets slots of `slot_minutes` from start to end."""
    __tablename__ = "school_ptm_days"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    term_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("academic_terms.id", ondelete="CASCADE"), nullable=False, index=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    slot_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PtmSlot(Base):
    """One class teacher's time on a PTM day; a parent books it for one child (one booking per child per day)."""
    __tablename__ = "school_ptm_slots"
    __table_args__ = (UniqueConstraint("ptm_day_id", "employee_id", "start_time", name="uq_school_ptm_slots_day_teacher_time"),
                      UniqueConstraint("ptm_day_id", "student_id", name="uq_school_ptm_slots_day_student"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    ptm_day_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("school_ptm_days.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    student_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("students.id", ondelete="SET NULL"), nullable=True)
    booked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    attended: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


# ---------------------------------------------------------------- stage 3: exams, report cards, promotion (app/school/results.py)
class ExamKind(str, enum.Enum):
    FIRST_TERM = "FIRST_TERM"
    MID_TERM = "MID_TERM"
    FINAL = "FINAL"


class SchoolExam(Base):
    """One of the session's three exams. Subject teachers enter marks; the office publishes it class by class."""
    __tablename__ = "school_exams"
    __table_args__ = (UniqueConstraint("term_id", "kind", name="uq_school_exams_term_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    term_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("academic_terms.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[ExamKind] = mapped_column(Enum(ExamKind, name="school_exam_kind"), nullable=False)
    marks_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)      # teachers may enter / change marks
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SchoolExamSubject(Base):
    """A subject's total marks in one exam (default 100; e.g. Drawing 50)."""
    __tablename__ = "school_exam_subjects"

    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("school_exams.id", ondelete="CASCADE"), primary_key=True)
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True)
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), nullable=False, index=True)
    total_marks: Mapped[int] = mapped_column(Integer, nullable=False, default=100)


class SchoolMark(Base):
    __tablename__ = "school_marks"
    __table_args__ = (UniqueConstraint("exam_id", "student_id", "course_id", name="uq_school_marks_exam_student_course"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("school_exams.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    marks: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    absent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    entered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SchoolSkill(Base):
    """A skills & behaviour line on report cards (rated A-D by the class teacher). The admin can change the list."""
    __tablename__ = "school_skills"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ReportCardNote(Base):
    """The class teacher's remarks and skills ratings for one student in one exam."""
    __tablename__ = "school_report_notes"

    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("school_exams.id", ondelete="CASCADE"), primary_key=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True)
    remarks: Mapped[str | None] = mapped_column(String(600), nullable=True)
    skills: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)          # {skill id: "A" | "B" | "C" | "D"}
    entered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ReportPublication(Base):
    """An exam's report cards of one class, published to students and parents (kept on record; can be withdrawn)."""
    __tablename__ = "school_report_publications"

    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("school_exams.id", ondelete="CASCADE"), primary_key=True)
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), primary_key=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PromotionDecision(str, enum.Enum):
    PROMOTED = "PROMOTED"                      # passed
    PROMOTED_ON_APPROVAL = "PROMOTED_ON_APPROVAL"   # did not pass, moved up by the office's decision
    KEPT_BACK = "KEPT_BACK"                    # repeats the class


class SchoolPromotion(Base):
    """Who moved from which class to which, for which session, and why. Never deleted."""
    __tablename__ = "school_promotions"
    __table_args__ = (UniqueConstraint("student_id", "from_term_id", name="uq_school_promotions_student_term"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    from_term_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("academic_terms.id", ondelete="CASCADE"), nullable=False)
    to_term_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("academic_terms.id", ondelete="CASCADE"), nullable=False)
    from_program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), nullable=False)
    to_program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), nullable=False)
    decision: Mapped[PromotionDecision] = mapped_column(Enum(PromotionDecision, name="school_promotion_decision"), nullable=False)
    percentage: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# ---------------------------------------------------------------------------------------------- stage 4: fees
class SchoolFeePlan(Base):
    """A class's fees for one session: the monthly tuition, the admission fee (once, when a new student joins) and the
    annual charges (on the student's first monthly voucher of the session). Changes count for vouchers made later."""
    __tablename__ = "school_fee_plans"

    term_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("academic_terms.id", ondelete="CASCADE"), primary_key=True)
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), primary_key=True)
    monthly_tuition: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    admission_fee: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    annual_charges: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class TransportRoute(Base):
    """A van / bus route with its monthly fee. Switched off, never deleted."""
    __tablename__ = "school_transport_routes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    monthly_fee: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StudentTransport(Base):
    """A student on a route from a month (the 1st) until a month (the 1st of the last month charged; empty = still on it)."""
    __tablename__ = "school_student_transport"
    __table_args__ = (Index("ix_school_student_transport_student", "student_id", "from_month"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    route_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("school_transport_routes.id", ondelete="RESTRICT"), nullable=False)
    from_month: Mapped[date] = mapped_column(Date, nullable=False)
    until_month: Mapped[date | None] = mapped_column(Date, nullable=True)
    added_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SchoolVoucherKind(str, enum.Enum):
    MONTHLY = "MONTHLY"
    ADMISSION = "ADMISSION"


class SchoolVoucher(Base):
    """What went on a school fee voucher, line by line (the money itself is the fee ledger's DEBIT, its challan and,
    for the sibling discount, a CONCESSION). One monthly voucher per student per month; one admission voucher ever."""
    __tablename__ = "school_vouchers"
    __table_args__ = (UniqueConstraint("student_id", "kind", "month", name="uq_school_vouchers_student_kind_month"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[SchoolVoucherKind] = mapped_column(Enum(SchoolVoucherKind, name="school_voucher_kind"), nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False)                  # the 1st of the month (admission: the month joined)
    term_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("academic_terms.id", ondelete="CASCADE"), nullable=False)
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), nullable=False)
    tuition: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    transport: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    route_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    annual: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    admission: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    sibling_discount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    sibling_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)    # 2 = second child, 3 = third ...
    ledger_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fee_ledger_entries.id", ondelete="SET NULL"), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
