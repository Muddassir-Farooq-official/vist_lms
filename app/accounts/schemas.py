from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.accounts.models import EntryKind, ExpenseSource, ExpenseStatus, PaymentMethod


class CategoryIn(BaseModel):
    kind: EntryKind
    name: str = Field(min_length=2, max_length=100)


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    is_active: bool | None = None


class CategoryOut(BaseModel):
    id: uuid.UUID
    kind: EntryKind
    name: str
    is_system: bool
    is_active: bool


class VendorIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    contact_person: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=150)
    address: str | None = Field(default=None, max_length=300)
    tax_number: str | None = Field(default=None, max_length=40)


class VendorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    contact_person: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=150)
    address: str | None = Field(default=None, max_length=300)
    tax_number: str | None = Field(default=None, max_length=40)
    is_active: bool | None = None


class VendorOut(VendorIn):
    id: uuid.UUID
    is_active: bool
    total_spent: float = 0.0


class AttachmentOut(BaseModel):
    id: uuid.UUID
    original_name: str
    content_type: str
    size_bytes: int
    is_image: bool
    inline: bool


class ExpenseOut(BaseModel):
    id: uuid.UUID
    reference: str
    category_id: uuid.UUID
    category_name: str
    vendor_id: uuid.UUID | None
    vendor_name: str | None
    title: str
    description: str | None
    amount: float
    expense_date: date
    payment_method: PaymentMethod
    reference_no: str | None
    status: ExpenseStatus
    source: ExpenseSource
    recorded_by_name: str | None
    decided_by_name: str | None
    decided_at: datetime | None
    decision_note: str | None
    voided_at: datetime | None
    void_reason: str | None
    created_at: datetime
    attachments: list[AttachmentOut] = []


class IncomeIn(BaseModel):
    category_id: uuid.UUID
    received_from: str = Field(min_length=2, max_length=150)
    amount: Decimal = Field(gt=0, le=100_000_000)
    income_date: date
    payment_method: PaymentMethod
    reference_no: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=1000)


class IncomeOut(BaseModel):
    id: uuid.UUID
    reference: str
    category_id: uuid.UUID
    category_name: str
    received_from: str
    amount: float
    income_date: date
    payment_method: PaymentMethod
    reference_no: str | None
    description: str | None
    recorded_by_name: str | None
    voided_at: datetime | None
    void_reason: str | None
    created_at: datetime


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class BookRow(BaseModel):
    kind: EntryKind
    id: uuid.UUID
    reference: str
    date: date
    title: str
    party: str | None
    category: str
    amount: float
    payment_method: str          # accounts methods, or a fee method (BANK_DEPOSIT, ONE_BILL...)
    status: str
    bank_reference: str | None = None     # the slip / cheque / transaction number, as on the bank statement
    by: uuid.UUID | None = None           # who recorded it (the cashier, for cash)


class CategoryTotal(BaseModel):
    category: str
    amount: float
    count: int


class MonthPoint(BaseModel):
    month: str                  # 2026-09
    fees: float
    fines: float = 0.0
    other_income: float
    income: float
    expenses: float


class VendorTotal(BaseModel):
    vendor: str
    amount: float
    count: int


class PendingTotal(BaseModel):
    count: int
    amount: float


class FineSourceTotal(BaseModel):
    source: str
    amount: float
    count: int


class SummaryOut(BaseModel):
    from_date: date | None
    to_date: date | None
    fees_collected: float
    fines_collected: float = 0.0
    fines_by_source: list[FineSourceTotal] = []
    fines_outstanding: PendingTotal = PendingTotal(count=0, amount=0.0)
    fines_waived: float = 0.0
    other_income: float
    total_income: float
    expenses: float
    net: float
    income_by_category: list[CategoryTotal]
    expenses_by_category: list[CategoryTotal]
    pending_expenses: PendingTotal
    monthly: list[MonthPoint]
    top_vendors: list[VendorTotal]


class ConfigOut(BaseModel):
    expense_approval_limit: float
    financial_year_start_month: int
    today: date