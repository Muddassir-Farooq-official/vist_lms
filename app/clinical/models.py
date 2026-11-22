from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Time, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ClinicalSite(Base):
    """A hospital or clinic where students train."""
    __tablename__ = "clinical_sites"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    contact_person: Mapped[str | None] = mapped_column(String(120), nullable=True)   # who the placement letter is addressed to
    contact_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class ClinicalUnit(Base):
    """A ward or department of a site, with how many students it takes at once."""
    __tablename__ = "clinical_units"
    __table_args__ = (UniqueConstraint("site_id", "name", name="uq_clinical_units_site_name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical_sites.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=10)


class ClinicalSupervisor(Base):
    """Someone who supervises groups: an employee (their own login) or a hospital's staff member given a limited login."""
    __tablename__ = "clinical_supervisors"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clinical_sites.id", ondelete="SET NULL"), nullable=True)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class ClinicalGroup(Base):
    """Students who rotate together (e.g. "BSN 6th Group A")."""
    __tablename__ = "clinical_groups"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    program_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("programs.id", ondelete="SET NULL"), nullable=True)
    semester: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ClinicalGroupMember(Base):
    __tablename__ = "clinical_group_members"

    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical_groups.id", ondelete="CASCADE"), primary_key=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), primary_key=True, index=True)


class ClinicalRotation(Base):
    """One group at one ward for a date range, on some weekdays, with a supervisor."""
    __tablename__ = "clinical_rotations"
    __table_args__ = (Index("ix_clinical_rotations_dates", "start_date", "end_date"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    unit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical_units.id", ondelete="RESTRICT"), nullable=False, index=True)
    supervisor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clinical_supervisors.id", ondelete="SET NULL"), nullable=True, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    days: Mapped[str] = mapped_column(String(40), nullable=False, default="0,1,2,3,4,5")     # Python weekdays, Monday = 0
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ClinicalAttendance(Base):
    """A student's attendance on one day of a rotation (kept apart from class attendance)."""
    __tablename__ = "clinical_attendance"
    __table_args__ = (UniqueConstraint("rotation_id", "student_id", "day", name="uq_clinical_attendance_rotation_student_day"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    rotation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical_rotations.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False)                 # PRESENT | ABSENT | LEAVE
    marked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    marked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ClinicalSkill(Base):
    """A procedure or case students must log, with how many times (per program, optionally from a semester)."""
    __tablename__ = "clinical_skills"
    __table_args__ = (UniqueConstraint("program_id", "name", name="uq_clinical_skills_program_name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    program_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    required: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    semester: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class LogEntry(Base):
    """A student's logbook entry: done a skill (times) on a day, signed off by the rotation's supervisor."""
    __tablename__ = "clinical_log_entries"
    __table_args__ = (Index("ix_clinical_log_entries_student", "student_id"), Index("ix_clinical_log_entries_rotation_status", "rotation_id", "status"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), nullable=False)
    skill_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical_skills.id", ondelete="RESTRICT"), nullable=False)
    rotation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clinical_rotations.id", ondelete="CASCADE"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    times: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="SUBMITTED")     # SUBMITTED | SIGNED | REJECTED
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
