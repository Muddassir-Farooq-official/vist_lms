"""Fixed assets: long-lasting things the institute owns (furniture, computers, lab equipment, vehicles, buildings, land).

Different from the store (app/inventory): the store counts consumables by quantity; here EVERY physical item has its
own tag number (e.g. VIST-IT-0042), a place, a holder, a cost and a book value that falls with depreciation.

- Cost and depreciation are never edited in place. Depreciation is posted month by month as DepreciationEntry rows;
  book value = cost - accumulated depreciation before the system (opening) - the posted entries.
- An asset bought before the system is brought in with its known cost and purchase date (the system works out the
  depreciation already used up), or, when the cost is not known, with an estimated value on the go-live date.
- Every change of place, holder, condition or status is an AssetEvent row: the asset's history.
- A physical check (AssetVerification) snapshots what should be in a room, records what is found, and closes with
  the list of what is missing or in the wrong place.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class DepreciationMethod(str, enum.Enum):
    STRAIGHT_LINE = "STRAIGHT_LINE"          # the same amount every year: (cost - residual value) / useful life
    REDUCING_BALANCE = "REDUCING_BALANCE"    # a fixed % of what is left each year (the book value at the start of the year)
    NONE = "NONE"                            # not depreciated (land)


class AssetStatus(str, enum.Enum):
    IN_USE = "IN_USE"
    IN_STORE = "IN_STORE"          # kept, not in use
    UNDER_REPAIR = "UNDER_REPAIR"
    MISSING = "MISSING"            # not found at a physical check; still on the books until written off
    DISPOSED = "DISPOSED"          # sold, scrapped, donated, written off: off the books


class AssetCondition(str, enum.Enum):
    GOOD = "GOOD"
    FAIR = "FAIR"
    POOR = "POOR"
    UNUSABLE = "UNUSABLE"


class AssetSource(str, enum.Enum):
    PURCHASE = "PURCHASE"          # bought through a purchase order
    OPENING = "OPENING"            # already owned when the system started
    DONATION = "DONATION"
    OTHER = "OTHER"


class DisposalMethod(str, enum.Enum):
    SOLD = "SOLD"
    SCRAPPED = "SCRAPPED"
    DONATED = "DONATED"
    LOST = "LOST"                  # lost or stolen
    WRITTEN_OFF = "WRITTEN_OFF"


class AssetCategory(UUIDPKMixin, TimestampMixin, Base):
    """A kind of asset and how it is depreciated (an asset may override the method, life or rate)."""

    __tablename__ = "asset_categories"
    __table_args__ = (UniqueConstraint("code", name="uq_asset_categories_code"),
                      CheckConstraint("rate_percent IS NULL OR (rate_percent > 0 AND rate_percent <= 100)", name="rate_range"),
                      CheckConstraint("useful_life_years IS NULL OR useful_life_years > 0", name="life_positive"))

    code: Mapped[str] = mapped_column(String(10), nullable=False)                    # used in tag numbers: VIST-IT-0001
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    method: Mapped[DepreciationMethod] = mapped_column(Enum(DepreciationMethod, name="depreciation_method"), nullable=False)
    useful_life_years: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)     # straight line
    rate_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)          # reducing balance, per year
    residual_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0, server_default="0")   # value left at the end, % of cost
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())


class AssetLocation(UUIDPKMixin, TimestampMixin, Base):
    """A room, lab, office or building."""

    __tablename__ = "asset_locations"
    __table_args__ = (UniqueConstraint("name", name="uq_asset_locations_name"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    building: Mapped[str | None] = mapped_column(String(120), nullable=True)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())


class Asset(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("tag", name="uq_assets_tag"),
        Index("ix_assets_category", "category_id"),
        Index("ix_assets_location", "location_id"),
        Index("ix_assets_status", "status"),
        CheckConstraint("cost >= 0", name="cost_not_negative"),
        CheckConstraint("opening_accumulated >= 0", name="opening_not_negative"),
    )

    tag: Mapped[str] = mapped_column(String(30), nullable=False)                      # VIST-IT-0042 (printed on the label)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset_categories.id", ondelete="RESTRICT"), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    make_model: Mapped[str | None] = mapped_column(String(150), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(100), nullable=True)   # the maker's serial number
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("asset_locations.id", ondelete="RESTRICT"), nullable=True)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    custodian_employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)   # who it is issued to
    status: Mapped[AssetStatus] = mapped_column(Enum(AssetStatus, name="asset_status"), nullable=False, default=AssetStatus.IN_USE)
    condition: Mapped[AssetCondition] = mapped_column(Enum(AssetCondition, name="asset_condition"), nullable=False, default=AssetCondition.GOOD)
    warranty_until: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Where it came from and what it cost.
    source: Mapped[AssetSource] = mapped_column(Enum(AssetSource, name="asset_source"), nullable=False, default=AssetSource.PURCHASE)
    acquired_on: Mapped[date | None] = mapped_column(Date, nullable=True)            # unknown for some old assets
    cost: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)               # purchase cost, or the estimated value on the go-live date
    cost_is_estimate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True)
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_orders.id", ondelete="SET NULL"), nullable=True)
    purchase_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_items.id", ondelete="SET NULL"), nullable=True)
    invoice_no: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # Depreciation: from depreciation_from onwards the system posts it; before that, opening_accumulated holds what was
    # already used up (worked out when the asset was entered, for an old asset with a known cost and date).
    depreciation_from: Mapped[date] = mapped_column(Date, nullable=False)
    opening_accumulated: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    method: Mapped[DepreciationMethod | None] = mapped_column(Enum(DepreciationMethod, name="depreciation_method", create_type=False), nullable=True)   # empty = category's
    useful_life_years: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    rate_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    residual_value: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)                                   # empty = category's % of cost

    disposed_on: Mapped[date | None] = mapped_column(Date, nullable=True)       # set when an approved disposal takes it off the books

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AssetEvent(UUIDPKMixin, Base):
    """The asset's history: added, moved, given to someone, condition or status changed, repaired, checked, disposed. Never edited."""

    __tablename__ = "asset_events"
    __table_args__ = (Index("ix_asset_events_asset", "asset_id"),)

    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)          # ADDED MOVED CUSTODIAN CONDITION STATUS REPAIR VERIFIED DISPOSED CORRECTED
    happened_on: Mapped[date] = mapped_column(Date, nullable=False)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)      # "Room 12 -> Computer Lab 2"
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cost: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)      # a repair's cost
    vendor_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DepreciationRun(UUIDPKMixin, TimestampMixin, Base):
    """Depreciation posted for every asset up to the end of one month."""

    __tablename__ = "depreciation_runs"
    # One live run per month; a reversed run stays as history and the month can be run again.
    __table_args__ = (Index("uq_depreciation_runs_live_period", "period_end", unique=True, postgresql_where=text("reversed_at IS NULL")),)

    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    total: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    assets_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    posted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reverse_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)


class DepreciationEntry(UUIDPKMixin, Base):
    """One asset's depreciation for a stretch of days (normally one month; longer when months were caught up)."""

    __tablename__ = "depreciation_entries"
    __table_args__ = (Index("ix_depreciation_entries_asset", "asset_id"), Index("ix_depreciation_entries_run", "run_id"),
                      CheckConstraint("amount >= 0", name="amount_not_negative"))

    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("depreciation_runs.id", ondelete="RESTRICT"), nullable=True)   # empty = the last part, at disposal
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    book_value_after: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssetDisposal(UUIDPKMixin, TimestampMixin, Base):
    """Taking an asset off the books (sold, scrapped, donated, lost, written off): asked for by the office, approved by the administration.

    On approval the last part of the depreciation is posted up to the disposal date, the book value at that date is
    kept, and money received (a sale) is recorded as income in Accounts. Gain or loss = proceeds - book value.
    """

    __tablename__ = "asset_disposals"
    __table_args__ = (Index("ix_asset_disposals_asset", "asset_id"), Index("ix_asset_disposals_status", "status"),
                      CheckConstraint("proceeds >= 0", name="proceeds_not_negative"))

    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False)
    method: Mapped[DisposalMethod] = mapped_column(Enum(DisposalMethod, name="asset_disposal_method"), nullable=False)
    disposed_on: Mapped[date] = mapped_column(Date, nullable=False)
    proceeds: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    payment_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    buyer: Mapped[str | None] = mapped_column(String(150), nullable=True)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="REQUESTED")     # REQUESTED APPROVED REJECTED
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    book_value: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)        # on the disposal date, kept at approval
    income_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("income_entries.id", ondelete="SET NULL"), nullable=True)


class AssetVerification(UUIDPKMixin, TimestampMixin, Base):
    """A physical check of what is in one place (or everywhere)."""

    __tablename__ = "asset_verifications"

    title: Mapped[str] = mapped_column(String(150), nullable=False)
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("asset_locations.id", ondelete="SET NULL"), nullable=True)   # empty = every place
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="OPEN")      # OPEN or CLOSED
    started_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    closed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class AssetVerificationLine(UUIDPKMixin, Base):
    __tablename__ = "asset_verification_lines"
    __table_args__ = (UniqueConstraint("verification_id", "asset_id", name="uq_asset_verification_lines_asset"),
                      Index("ix_asset_verification_lines_verification", "verification_id"))

    verification_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset_verifications.id", ondelete="CASCADE"), nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False)
    expected: Mapped[bool] = mapped_column(Boolean, nullable=False)                     # on the list when the check started
    expected_location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("asset_locations.id", ondelete="SET NULL"), nullable=True)
    result: Mapped[str] = mapped_column(String(20), nullable=False, default="NOT_CHECKED")   # NOT_CHECKED FOUND WRONG_PLACE MISSING
    found_location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("asset_locations.id", ondelete="SET NULL"), nullable=True)
    condition: Mapped[AssetCondition | None] = mapped_column(Enum(AssetCondition, name="asset_condition", create_type=False), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    checked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
