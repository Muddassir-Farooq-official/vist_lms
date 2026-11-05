"""Class cover: another teacher (or member of staff) takes an absent teacher's class.

One row per covered class. The amount is worked out when the cover is given (the absent teacher's day's pay shared by
their classes that day) and copied here, so a later salary change never rewrites it. It is paid on the substitute's
payslip only once the class was actually held (its attendance was taken), and only once.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class ClassCover(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "class_covers"
    __table_args__ = (
        # At most one live cover per class (a replaced or cancelled one stays as history).
        Index("uq_class_covers_session_active", "class_session_id", unique=True, postgresql_where=text("status = 'ACTIVE'")),
        Index("ix_class_covers_substitute_date", "substitute_employee_id", "cover_date"),
        Index("ix_class_covers_date", "cover_date"),
    )

    class_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("class_sessions.id", ondelete="CASCADE"), nullable=False)
    cover_date: Mapped[date] = mapped_column(Date, nullable=False)
    absent_employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    substitute_employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)          # "BIO101 on 05 Nov 2026 at 09:00, for Ali Raza", as printed
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    amount_note: Mapped[str | None] = mapped_column(String(200), nullable=True)      # how the amount was worked out
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="ACTIVE")   # ACTIVE | CANCELLED
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)