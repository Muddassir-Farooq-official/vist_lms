"""Transport: routes with their stops, and which student rides which route (with dates and the agreed monthly fee).
The monthly transport fee is prepared as a draft and approved by the accounts office (app/monthly_charges)."""
from __future__ import annotations

import uuid
from datetime import date, time

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, Integer, Numeric, String, Time, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class TransportRoute(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "transport_routes"
    __table_args__ = (UniqueConstraint("name", name="uq_transport_routes_name"), CheckConstraint("monthly_fee >= 0", name="ck_transport_routes_fee"))

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    vehicle: Mapped[str | None] = mapped_column(String(60), nullable=True)          # e.g. Coaster LES-1234
    seats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    driver_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    driver_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    monthly_fee: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class TransportStop(UUIDPKMixin, Base):
    __tablename__ = "transport_stops"
    __table_args__ = (UniqueConstraint("route_id", "name", name="uq_transport_stops_route_name"),)

    route_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transport_routes.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pickup_time: Mapped[time | None] = mapped_column(Time, nullable=True)


class TransportRider(UUIDPKMixin, TimestampMixin, Base):
    """One student on one route from a date until they stop (end_date set)."""

    __tablename__ = "transport_riders"
    __table_args__ = (
        Index("ix_transport_riders_route", "route_id"),
        Index("uq_transport_riders_one_current", "student_id", unique=True, postgresql_where=text("end_date IS NULL")),
    )

    route_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transport_routes.id", ondelete="RESTRICT"), nullable=False)
    stop_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("transport_stops.id", ondelete="SET NULL"), nullable=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), nullable=False, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    monthly_fee: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
