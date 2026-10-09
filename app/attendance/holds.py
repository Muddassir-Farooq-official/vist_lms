"""Holds placed on a student while an attendance fine is unpaid.

Replaces the old "disable the whole login" behaviour: a blocked student could
not even see the challan or fine they had to pay. Now the login stays open and
only self-enrollment and the result card are held.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleViolationError


async def unpaid_attendance_fines(session: AsyncSession, student_id: uuid.UUID) -> tuple[int, float]:
    from app.fines.models import Fine, FinePayerType, FineSource, FineStatus

    count, total = (await session.execute(
        select(func.count(), func.coalesce(func.sum(Fine.amount), 0)).where(
            Fine.payer_type == FinePayerType.STUDENT, Fine.payer_id == student_id,
            Fine.source == FineSource.ATTENDANCE, Fine.status == FineStatus.PENDING)
    )).one()
    return int(count), float(total)


async def ensure_no_attendance_hold(session: AsyncSession, student_id: uuid.UUID, *, action: str) -> None:
    count, total = await unpaid_attendance_fines(session, student_id)
    if count:
        raise BusinessRuleViolationError(
            f"{action} is on hold: you have {count} unpaid attendance fine(s) totalling Rs. {total:,.0f}. "
            "Pay it at the accounts office (see My Fines); the hold lifts as soon as it is marked paid.",
            details={"hold": "ATTENDANCE_FINE", "count": count, "amount": total},
        )