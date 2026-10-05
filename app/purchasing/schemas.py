from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.accounts.models import PaymentMethod
from app.purchasing.models import PurchaseStatus


class ItemIn(BaseModel):
    description: str = Field(min_length=2, max_length=200)
    quantity: Decimal = Field(gt=0, le=100000)
    estimated_unit_price: Decimal = Field(gt=0, le=100_000_000)


class PurchaseIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    justification: str = Field(min_length=5, max_length=2000)
    needed_by: date | None = None
    category_id: uuid.UUID | None = None
    suggested_vendor_id: uuid.UUID | None = None
    items: list[ItemIn] = Field(min_length=1, max_length=30)


class OrderIn(BaseModel):
    vendor_id: uuid.UUID
    expected_delivery: date | None = None
    note: str | None = Field(default=None, max_length=500)


class ReceiveLine(BaseModel):
    item_id: uuid.UUID
    received_quantity: Decimal = Field(ge=0, le=100000)
    actual_unit_price: Decimal = Field(ge=0, le=100_000_000)


class ReceiveIn(BaseModel):
    invoice_no: str = Field(min_length=1, max_length=80)
    lines: list[ReceiveLine] = Field(min_length=1, max_length=30)


class ItemOut(BaseModel):
    id: uuid.UUID
    description: str
    quantity: float
    estimated_unit_price: float
    estimated_total: float
    received_quantity: float | None
    actual_unit_price: float | None
    actual_total: float | None


class PurchaseOut(BaseModel):
    id: uuid.UUID
    reference: str
    title: str
    justification: str
    needed_by: date | None
    status: PurchaseStatus
    requested_by_name: str | None
    category_id: uuid.UUID | None
    category_name: str | None
    suggested_vendor_name: str | None
    vendor_id: uuid.UUID | None
    vendor_name: str | None
    estimated_total: float
    actual_total: float | None
    over_estimate_percent: float | None
    items: list[ItemOut]
    created_at: datetime
    decided_by_name: str | None
    decided_at: datetime | None
    decision_note: str | None
    ordered_at: datetime | None
    expected_delivery: date | None
    order_note: str | None
    received_at: datetime | None
    invoice_no: str | None
    paid_at: datetime | None
    payment_method: PaymentMethod | None
    payment_reference: str | None
    expense_id: uuid.UUID | None
    expense_reference: str | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    actions: list[str]


class PurchaseSummary(BaseModel):
    waiting_for_approval: int
    to_order: int
    on_order: int
    to_pay: int
    owed_to_vendors: float
    paid_total: float