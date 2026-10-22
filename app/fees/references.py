"""One bank slip / transaction / cheque number pays for one thing: refuse a reference that was already used."""
from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleViolationError


async def refuse_reused_reference(session: AsyncSession, reference: str | None, method: str | None) -> None:
    """Cash has no reference. For any other method, the same reference may not already appear on a recorded
    fee payment or on a fine that was paid."""
    ref = (reference or "").strip()
    if not ref or (method or "CASH").upper() == "CASH":
        return
    from app.fees.models import FeeLedgerEntry, LedgerEntryType
    from app.fines.models import Fine, FineStatus

    def squash(column):
        return func.regexp_replace(func.lower(column), r"[^a-z0-9]", "", "g")

    wanted = re.sub(r"[^a-z0-9]", "", ref.lower())       # "HBL 5566" and "hbl5566" are the same slip
    if not wanted:
        return
    in_fees = (await session.execute(select(func.count()).select_from(FeeLedgerEntry).where(
        FeeLedgerEntry.entry_type == LedgerEntryType.CREDIT, FeeLedgerEntry.payment_reference.is_not(None), squash(FeeLedgerEntry.payment_reference) == wanted))).scalar_one()
    in_fines = (await session.execute(select(func.count()).select_from(Fine).where(
        Fine.status == FineStatus.PAID, Fine.payment_reference.is_not(None), squash(Fine.payment_reference) == wanted))).scalar_one()
    if in_fees or in_fines:
        raise BusinessRuleViolationError(f"The reference '{ref}' is already used for another payment. Check the slip or transaction number.")
