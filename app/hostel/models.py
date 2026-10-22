"""Hostel: rooms, who lives in which room (with dates), and the monthly hostel charge.

A student's monthly charge is added to their dues (a HOSTEL charge they pay at the accounts office like
any other), once per month per stay; it shows in the accounts as income when paid."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class HostelRoom(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "hostel_rooms"
    __table_args__ = (UniqueConstraint("code", name="uq_hostel_rooms_code"), CheckConstraint("capacity > 0", name="ck_hostel_rooms_capacity_positive"))

    code: Mapped[str] = mapped_column(String(30), nullable=False)               # e.g. B-12
    block: Mapped[str | None] = mapped_column(String(60), nullable=True)        # e.g. Girls hostel, Block B
    floor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    gender: Mapped[str] = mapped_column(String(10), nullable=False, default="ANY")   # MALE / FEMALE / ANY
    monthly_fee: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)


class HostelStay(UUIDPKMixin, TimestampMixin, Base):
    """One student in one room from a date until they move out (end_date set)."""

    __tablename__ = "hostel_stays"
    __table_args__ = (
        Index("ix_hostel_stays_room", "room_id"),
        Index("ix_hostel_stays_student", "student_id"),
        Index("uq_hostel_stays_one_current_per_student", "student_id", unique=True, postgresql_where=text("end_date IS NULL")),
    )

    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hostel_rooms.id", ondelete="RESTRICT"), nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="RESTRICT"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    bed_no: Mapped[str | None] = mapped_column(String(10), nullable=True)
    monthly_fee: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)        # agreed for this stay (copied from the room, can differ)
    end_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class HostelCharge(UUIDPKMixin, TimestampMixin, Base):
    """The month's charge for one stay: never charged twice for the same month."""

    __tablename__ = "hostel_charges"
    __table_args__ = (UniqueConstraint("stay_id", "month", name="uq_hostel_charges_stay_month"),)

    stay_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hostel_stays.id", ondelete="RESTRICT"), nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False)                          # first day of the month
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    fine_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fines.id", ondelete="SET NULL"), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)