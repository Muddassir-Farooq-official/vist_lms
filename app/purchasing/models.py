"""Buying things for the institute: a staff member asks, the administration approves, the accounts office orders, receives and pays."""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Identity, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.accounts.models import PaymentMethod
from app.core.database import Base, TimestampMixin, UUIDPKMixin


class PurchaseStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"      # asked for, waiting for the administration
    APPROVED = "APPROVED"        # may be bought; the accounts office orders it
    REJECTED = "REJECTED"
    ORDERED = "ORDERED"          # ordered from a vendor
    RECEIVED = "RECEIVED"        # the goods arrived; the bill is owed to the vendor
    PAID = "PAID"                # paid: it is now an expense in the books
    CANCELLED = "CANCELLED"


class PurchaseOrder(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (Index("ix_purchase_orders_status", "status"), Index("ix_purchase_orders_requested_by", "requested_by"))

    serial_no: Mapped[int] = mapped_column(Integer, Identity(), unique=True, nullable=False)           # PO-2026-00012
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    needed_by: Mapped[date | None] = mapped_column(Date, nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("account_categories.id", ondelete="SET NULL"), nullable=True)
    suggested_vendor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[PurchaseStatus] = mapped_column(Enum(PurchaseStatus, name="purchase_status"), nullable=False, default=PurchaseStatus.REQUESTED)
    estimated_total: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    actual_total: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    ordered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_delivery: Mapped[date | None] = mapped_column(Date, nullable=True)
    order_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invoice_no: Mapped[str | None] = mapped_column(String(80), nullable=True)
    paid_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_method: Mapped[PaymentMethod | None] = mapped_column(Enum(PaymentMethod, name="account_payment_method", create_type=False), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    expense_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("expenses.id", ondelete="SET NULL"), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class PurchaseItem(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "purchase_items"
    __table_args__ = (Index("ix_purchase_items_order", "order_id"),)

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    estimated_unit_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    received_quantity: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    actual_unit_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)