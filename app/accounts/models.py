"""The institute's money apart from student fees: what it spends (expenses), and what else it receives (donations, canteen, rent...)."""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Enum, ForeignKey, Identity, Index, Integer, Numeric, String, Text, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class EntryKind(str, enum.Enum):
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"


class PaymentMethod(str, enum.Enum):
    CASH = "CASH"
    BANK_TRANSFER = "BANK_TRANSFER"
    CHEQUE = "CHEQUE"
    ONLINE = "ONLINE"


class ExpenseStatus(str, enum.Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"   # above the approval limit: waiting for the administration
    APPROVED = "APPROVED"                   # counts in the books
    REJECTED = "REJECTED"
    VOIDED = "VOIDED"                       # entered by mistake or reversed: kept on record, not counted


class ExpenseSource(str, enum.Enum):
    MANUAL = "MANUAL"
    PURCHASE = "PURCHASE"                   # paid for a purchase order (added with purchasing)
    PAYROLL = "PAYROLL"                     # a salary run (added with payroll)


class AccountCategory(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "account_categories"
    __table_args__ = (UniqueConstraint("kind", "name", name="uq_account_categories_kind_name"),)

    kind: Mapped[EntryKind] = mapped_column(Enum(EntryKind, name="account_entry_kind"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")    # used by the system itself: cannot be renamed or switched off
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())


class Vendor(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "vendors"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    contact_person: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(150), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    tax_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())


class Expense(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "expenses"
    __table_args__ = (Index("ix_expenses_date", "expense_date"), Index("ix_expenses_status", "status"), Index("ix_expenses_category", "category_id"))

    serial_no: Mapped[int] = mapped_column(Integer, Identity(), unique=True, nullable=False)           # EXP-2026-00042
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_categories.id", ondelete="RESTRICT"), nullable=False)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod, name="account_payment_method"), nullable=False)
    reference_no: Mapped[str | None] = mapped_column(String(80), nullable=True)                       # cheque number, bank reference, receipt number
    status: Mapped[ExpenseStatus] = mapped_column(Enum(ExpenseStatus, name="expense_status"), nullable=False, default=ExpenseStatus.APPROVED)
    source: Mapped[ExpenseSource] = mapped_column(Enum(ExpenseSource, name="expense_source"), nullable=False, default=ExpenseSource.MANUAL)
    source_ref_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExpenseAttachment(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "expense_attachments"
    __table_args__ = (Index("ix_expense_attachments_expense", "expense_id"),)

    expense_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("expenses.id", ondelete="CASCADE"), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)


class IncomeEntry(UUIDPKMixin, TimestampMixin, Base):
    """Money received other than student fees (those live in the fee ledger): donations, canteen, rent, hostel, grants..."""

    __tablename__ = "income_entries"
    __table_args__ = (Index("ix_income_entries_date", "income_date"),)

    serial_no: Mapped[int] = mapped_column(Integer, Identity(), unique=True, nullable=False)           # INC-2026-00042
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account_categories.id", ondelete="RESTRICT"), nullable=False)
    received_from: Mapped[str] = mapped_column(String(150), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    income_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod, name="account_payment_method", create_type=False), nullable=False)
    reference_no: Mapped[str | None] = mapped_column(String(80), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)