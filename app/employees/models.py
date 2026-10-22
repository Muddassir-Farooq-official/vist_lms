from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class EmployeeStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ON_LEAVE = "ON_LEAVE"
    TERMINATED = "TERMINATED"


class JobRole(UUIDPKMixin, TimestampMixin, Base):
    """Admin-managed catalog: Teacher, Librarian, Accountant, Hostel
    Warden, Admin Staff, etc. `default_role_code` is what RBAC role a new
    hire in this job role gets automatically — admin picks a job title,
    not an RBAC role, when creating an employee."""

    __tablename__ = "job_roles"
    __table_args__ = (UniqueConstraint("code", name="uq_job_roles_code"),)

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    default_role_code: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Employee(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("cnic", name="uq_employees_cnic"),
        UniqueConstraint("user_id", name="uq_employees_user_id"),
        UniqueConstraint("employee_code", name="uq_employees_employee_code"),
        Index("ix_employees_job_role_id", "job_role_id"),
        Index("ix_employees_status", "status"),
        CheckConstraint(r"cnic ~ '^[0-9]{5}-[0-9]{7}-[0-9]{1}$'", name="cnic_format"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    job_role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_roles.id", ondelete="RESTRICT"), nullable=False)

    # System-generated (EMP-0001, EMP-0002, ...), never admin-supplied —
    # see app/core/sequences.py. Doubles as the employee's permanent
    # login username, so it's immutable once created, same discipline as
    # Student.roll_number.
    employee_code: Mapped[str] = mapped_column(String(20), nullable=False)

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    cnic: Mapped[str] = mapped_column(String(15), nullable=False)
    contact_no: Mapped[str] = mapped_column(String(20), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    gender: Mapped[str] = mapped_column(String(20), nullable=False)
    hire_date: Mapped[date] = mapped_column(Date, nullable=False)
    left_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Where the salary goes (the payroll bank-transfer sheet)
    bank_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bank_account_title: Mapped[str | None] = mapped_column(String(150), nullable=True)
    bank_iban: Mapped[str | None] = mapped_column(String(34), nullable=True)      # IBAN (PK + 22) or the plain account number      # last working day, set when employment ends
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Added for real-institute launch (all nullable so existing rows stay valid).
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    blood_group: Mapped[str | None] = mapped_column(String(5), nullable=True)
    marital_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # PERMANENT | CONTRACT | VISITING | PART_TIME
    employment_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    emergency_contact_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    emergency_contact_no: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Address + religion — did not previously exist on Employee at all
    # (only Student had these); added per approved decision so faculty
    # profiles have the same "each column visible/editable" completeness
    # as student profiles. All nullable since existing employees predate
    # these fields and aren't forced to backfill.
    religion: Mapped[str | None] = mapped_column(String(50), nullable=True)
    province: Mapped[str | None] = mapped_column(String(100), nullable=True)
    district: Mapped[str | None] = mapped_column(String(100), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    address_line: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Same upload pattern as Student — see app/core/storage.py.
    photo_storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    photo_original_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    certificate_storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    certificate_original_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[EmployeeStatus] = mapped_column(
        Enum(EmployeeStatus, name="employee_status"), default=EmployeeStatus.ACTIVE, nullable=False
    )

    job_role: Mapped[JobRole] = relationship()
    salary_history: Mapped[list["EmployeeSalaryHistory"]] = relationship(
        back_populates="employee", cascade="all, delete-orphan"
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def has_photo(self) -> bool:
        return self.photo_storage_key is not None

    @property
    def has_certificate(self) -> bool:
        return self.certificate_storage_key is not None


class EmployeeSalaryHistory(UUIDPKMixin, Base):
    """Append-only, per the ledger/versioning discipline used everywhere
    financial in this system — a salary change is a NEW row with
    effective_from set, never an edit to the prior row. The current salary
    is whichever row has effective_to IS NULL."""

    __tablename__ = "employee_salary_history"
    __table_args__ = (
        Index("ix_employee_salary_history_employee_id", "employee_id"),
        CheckConstraint("base_salary >= 0", name="non_negative_base_salary"),
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    base_salary: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    # How this row came to be — INITIAL (first salary at hire), or a
    # manual increment expressed as either a flat amount or a percentage
    # of the prior salary. Purely descriptive/audit context; base_salary
    # above always holds the actual resulting absolute amount.
    change_type: Mapped[str] = mapped_column(String(20), nullable=False)  # INITIAL | FIXED_AMOUNT | PERCENTAGE
    change_value: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[date] = mapped_column(Date, nullable=False)

    employee: Mapped[Employee] = relationship(back_populates="salary_history")


class EmployeeBonus(UUIDPKMixin, Base):
    """One-time discretionary amount, separate from base salary."""

    __tablename__ = "employee_bonuses"
    __table_args__ = (
        Index("ix_employee_bonuses_employee_id", "employee_id"),
        CheckConstraint("amount >= 0", name="non_negative_amount"),
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    awarded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    awarded_at: Mapped[date] = mapped_column(Date, nullable=False)