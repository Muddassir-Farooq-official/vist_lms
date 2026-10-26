"""Paying staff: recurring pay items, salary advances, the monthly payroll run and each employee's payslip."""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Identity, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.accounts.models import PaymentMethod
from app.core.database import Base, TimestampMixin, UUIDPKMixin


class PayItemKind(str, enum.Enum):
    ALLOWANCE = "ALLOWANCE"
    DEDUCTION = "DEDUCTION"


class PayItemCalc(str, enum.Enum):
    FIXED = "FIXED"                          # a fixed amount every month
    PERCENT_OF_BASE = "PERCENT_OF_BASE"      # a percentage of the basic salary


class PayrollStatus(str, enum.Enum):
    DRAFT = "DRAFT"                          # being prepared: payslips can still be adjusted
    SUBMITTED = "SUBMITTED"                  # sent by the accounts office to the administration for approval: locked
    APPROVED = "APPROVED"                    # checked and locked, waiting to be paid
    PAID = "PAID"                            # paid: it is an expense in the books and the payslips are released
    CANCELLED = "CANCELLED"


class AdvanceStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"      # asked for by the accounts office, waiting for an administrator
    APPROVED = "APPROVED"        # approved, not yet handed over
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"      # called off before it was paid
    ACTIVE = "ACTIVE"            # paid out: being recovered from payslips
    CLEARED = "CLEARED"


class LineKind(str, enum.Enum):
    EARNING = "EARNING"
    DEDUCTION = "DEDUCTION"


class EmployeePayItem(UUIDPKMixin, TimestampMixin, Base):
    """A recurring allowance or deduction on one employee's pay (house rent, provident fund, tax...)."""

    __tablename__ = "employee_pay_items"
    __table_args__ = (Index("ix_employee_pay_items_employee", "employee_id"),)

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[PayItemKind] = mapped_column(Enum(PayItemKind, name="pay_item_kind"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    calc: Mapped[PayItemCalc] = mapped_column(Enum(PayItemCalc, name="pay_item_calc"), nullable=False, default=PayItemCalc.FIXED)
    value: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)


class SalaryAdvance(UUIDPKMixin, TimestampMixin, Base):
    """Money paid to an employee before payday, recovered a little from each payslip."""

    __tablename__ = "salary_advances"
    __table_args__ = (Index("ix_salary_advances_employee", "employee_id"),)

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    monthly_recovery: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    balance: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[AdvanceStatus] = mapped_column(Enum(AdvanceStatus, name="advance_status"), nullable=False, default=AdvanceStatus.ACTIVE)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    granted_on: Mapped[date] = mapped_column(Date, nullable=False)
    payment_method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod, name="account_payment_method", create_type=False), nullable=False)
    expense_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("expenses.id", ondelete="SET NULL"), nullable=True)
    # The approval trail (empty on advances made before approvals existed, which were paid at once).
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    paid_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)


class PayrollRun(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "payroll_runs"
    __table_args__ = (Index("uq_payroll_runs_month_active", "month", unique=True, postgresql_where=text("status <> 'CANCELLED'")),)

    serial_no: Mapped[int] = mapped_column(Integer, Identity(), unique=True, nullable=False)           # PAY-2026-10
    month: Mapped[date] = mapped_column(Date, nullable=False)                                          # the first day of the month it pays for
    working_days: Mapped[int] = mapped_column(Integer, nullable=False, default=26, server_default="26")   # a day's pay = basic / this (22 or 26...)
    status: Mapped[PayrollStatus] = mapped_column(Enum(PayrollStatus, name="payroll_status"), nullable=False, default=PayrollStatus.DRAFT)
    employees_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_gross: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total_deductions: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total_net: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    returned_reason: Mapped[str | None] = mapped_column(Text, nullable=True)     # why the administration sent it back to draft (last time)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_method: Mapped[PaymentMethod | None] = mapped_column(Enum(PaymentMethod, name="account_payment_method", create_type=False), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    expense_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("expenses.id", ondelete="SET NULL"), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Payslip(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "payslips"
    __table_args__ = (UniqueConstraint("run_id", "employee_id", name="uq_payslips_run_employee"), Index("ix_payslips_employee", "employee_id"))

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payroll_runs.id", ondelete="CASCADE"), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    employee_name: Mapped[str] = mapped_column(String(200), nullable=False)          # who they were then, as printed
    employee_code: Mapped[str] = mapped_column(String(40), nullable=False)
    job_title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    base_salary: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    days_in_month: Mapped[int] = mapped_column(Integer, nullable=False)
    days_payable: Mapped[int] = mapped_column(Integer, nullable=False)               # fewer than the month if they joined part-way
    unpaid_days: Mapped[float] = mapped_column(Numeric(4, 1), nullable=False, default=0, server_default="0")
    # From staff attendance (worked out when prepared / recalculated, or set by the administration when overridden)
    working_days: Mapped[int] = mapped_column(Integer, nullable=False, default=26, server_default="26")
    absent_days: Mapped[float] = mapped_column(Numeric(4, 1), nullable=False, default=0, server_default="0")
    casual_leaves: Mapped[float] = mapped_column(Numeric(4, 1), nullable=False, default=0, server_default="0")       # forgiven this month
    late_marks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")                  # late arrivals + early leavings
    attendance_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    attendance_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    adjustment: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")      # + adds to pay, - takes from it
    adjustment_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    held: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")             # left out of this payment
    held_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    released_on: Mapped[date | None] = mapped_column(Date, nullable=True)          # a held payslip paid on its own after the month was paid
    gross: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    deductions: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    net: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)


class PayslipLine(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "payslip_lines"
    __table_args__ = (Index("ix_payslip_lines_payslip", "payslip_id"),)

    payslip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payslips.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kind: Mapped[LineKind] = mapped_column(Enum(LineKind, name="payslip_line_kind"), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)                  # BASE, ALLOWANCE, BONUS, DEDUCTION, UNPAID, ADVANCE, ADJUSTMENT
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    ref_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)                  # the bonus or advance this line came from

class EmployeePayrollSetting(TimestampMixin, Base):
    """Per-employee payroll settings that differ from the institute's rules."""

    __tablename__ = "employee_payroll_settings"

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True)
    security_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)        # empty = the institute's %; 0 = none for this person
    security_opening: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")   # already held before this system
    security_released: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")  # paid back to the person (on leaving)
    security_released_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)