"""Creates the payable challan (with a unique consumer number) for one charge."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleViolationError
from app.fees.models import FeeAssignment, FeeLedgerEntry
from app.fees.payment_gateway import get_payment_gateway
from app.fees.payment_models import ChallanStatus, PaymentChallan


async def create_challan(session: AsyncSession, *, assignment: FeeAssignment, entry: FeeLedgerEntry, amount: float, due_date: date) -> PaymentChallan:
    gateway = get_payment_gateway()
    consumer_number = None
    for _ in range(5):
        candidate = await gateway.generate_consumer_number(
            reference=f"{assignment.payer_type.value}-{assignment.payer_id.hex[:8]}-{entry.installment_number}-{entry.id.hex[:6]}", amount=float(amount), due_date=due_date)
        if (await session.execute(select(PaymentChallan).where(PaymentChallan.consumer_number == candidate))).scalar_one_or_none() is None:
            consumer_number = candidate
            break
    if consumer_number is None:
        raise BusinessRuleViolationError("Could not generate a unique consumer number after several attempts. Try again.")
    challan = PaymentChallan(fee_ledger_entry_id=entry.id, fee_assignment_id=assignment.id, consumer_number=consumer_number, amount=float(amount), due_date=due_date,
                             status=ChallanStatus.PENDING, provider="1LINK_SIMULATED", generated_at=datetime.now(timezone.utc))
    session.add(challan)
    await session.flush()
    return challan