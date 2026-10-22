"""The store: things the institute keeps in stock (stationery, uniforms, lab items, books, laptops...).

Stock never changes silently. Every change is a StockMovement row -- received from a purchase,
issued to someone, returned, adjusted after a count, or a reversal of a mistake -- with who did it,
when, why, the cost, and (for paid issues) the receipt or the fine that charges it. Rows are never
edited or deleted; a mistake is undone by a new REVERSED movement. That append-only ledger is the audit trail.
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Identity, Index, Integer, Numeric, String, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class MovementKind(str, enum.Enum):
    OPENING = "OPENING"        # stock already on the shelf when the item was added
    RECEIVED = "RECEIVED"      # came in from a purchase order
    ISSUED = "ISSUED"          # handed to a student, staff member or department
    RETURNED = "RETURNED"      # came back from the person it was issued to
    ADJUSTED = "ADJUSTED"      # stock count correction, damage, loss (always with a reason)
    REVERSED = "REVERSED"      # undoes an earlier movement entered by mistake


class ChargeMode(str, enum.Enum):
    FREE = "FREE"              # given by the college at no charge (still costed, for the books)
    PAID_NOW = "PAID_NOW"      # paid at the counter: a receipt and an income entry
    ADD_TO_DUES = "ADD_TO_DUES"  # charged later: a fine the person pays like any other


class StockItem(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "stock_items"
    __table_args__ = (Index("ix_stock_items_category", "category"),)

    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)       # STK-0001
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False, default="General")
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="pcs")
    sale_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)   # default price when sold / charged
    reorder_level: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    # Lent out and expected back (laptops, lab coats, keys, library-style items): tracked as "with whom" until returned.
    is_returnable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    quantity_on_hand: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    average_cost: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")   # moving average, for stock value
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class StockMovement(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "stock_movements"
    __table_args__ = (
        Index("ix_stock_movements_item", "item_id"),
        Index("ix_stock_movements_party", "party_type", "party_id"),
        Index("ix_stock_movements_kind", "kind"),
    )

    serial_no: Mapped[int] = mapped_column(Integer, Identity(), unique=True, nullable=False)       # MOV-000123
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stock_items.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[MovementKind] = mapped_column(Enum(MovementKind, name="stock_movement_kind"), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)          # + into the store, - out of it
    unit_cost: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    balance_after: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    # Who it went to / came back from: STUDENT, EMPLOYEE or DEPARTMENT (a department has no id, only a name).
    party_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    party_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    party_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    charge_mode: Mapped[ChargeMode | None] = mapped_column(Enum(ChargeMode, name="stock_charge_mode"), nullable=True)
    unit_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    charge_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    receipt_number: Mapped[str | None] = mapped_column(String(30), nullable=True, unique=True)
    income_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("income_entries.id", ondelete="SET NULL"), nullable=True)
    fine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fines.id", ondelete="SET NULL"), nullable=True)
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_orders.id", ondelete="SET NULL"), nullable=True)
    purchase_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_items.id", ondelete="SET NULL"), nullable=True)
    issue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stock_movements.id", ondelete="SET NULL"), nullable=True)   # a RETURN: the issue it closes
    reverses_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stock_movements.id", ondelete="SET NULL"), nullable=True)  # a REVERSED row: what it undoes
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)