"""Move a payment that was recorded on the wrong fee to the right fee of the same person (accounts office asks, an
administrator approves on the Approvals page).

The ledger is append-only, so nothing is edited. Two linked rows are added, the same kind used to move advance credit
(the cash book ignores both, because no money moved):
  * CREDIT_OUT on the wrong fee, pointing at the payment it takes away (applies_to_entry_id), so that payment no longer
    pays anything there and its instalment is owed again;
  * CREDIT_IN on the right fee, dated the ORIGINAL payment day, so the right fee counts as paid on that day (no late
    fine there).
The receipt keeps its number and now names the right fee ("moved from ..."). The wrong fee gets a late-fee pause from
the payment day until 7 days after the move, so the move itself never causes a fine; after that normal fines apply.

Not allowed (reverse the payment and record it again instead): a payment that paid a bank challan, one already
reversed or moved, or on a fee that has had refunds or credit moved out.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.models import ApprovalRequest
from app.approvals.service import executor, gate, pending_response
from app.audit.service import AuditService
from app.core.clock import institute_today
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.fees.models import AssignmentStatus, FeeAssignment, FeeLedgerEntry, FeeStructure, LedgerEntryType

KIND = "PAYMENT_MOVE"
PAUSE_DAYS = 7


async def moved_to(session: AsyncSession, credit_id: uuid.UUID) -> FeeLedgerEntry | None:
    """The CREDIT_IN row a payment was moved to (None if it was not moved)."""
    out = (await session.execute(select(FeeLedgerEntry).where(
        FeeLedgerEntry.entry_type == LedgerEntryType.CREDIT_OUT, FeeLedgerEntry.applies_to_entry_id == credit_id))).scalars().first()
    if out is None or out.related_entry_id is None:
        return None
    return await session.get(FeeLedgerEntry, out.related_entry_id)


async def _checked(session: AsyncSession, credit_id: uuid.UUID, to_assignment_id: uuid.UUID, *, lock: bool) -> tuple:
    from app.fees.assignment_service import credit_is_reversed, lock_assignment
    from app.fees.payment_models import PaymentChallan

    credit = await session.get(FeeLedgerEntry, credit_id)
    if credit is None or credit.entry_type != LedgerEntryType.CREDIT or not credit.receipt_number:
        raise ResourceNotFoundError("Payment not found.")
    if credit.fee_assignment_id == to_assignment_id:
        raise BusinessRuleViolationError("The payment is already on that fee. Choose the fee it should have paid.")
    if lock:
        first, second = sorted([credit.fee_assignment_id, to_assignment_id], key=str)        # same order as every other money action
        locked = {first: await lock_assignment(session, first), second: await lock_assignment(session, second)}
        src, dst = locked[credit.fee_assignment_id], locked[to_assignment_id]
    else:
        src, dst = await session.get(FeeAssignment, credit.fee_assignment_id), await session.get(FeeAssignment, to_assignment_id)
    if dst is None:
        raise ResourceNotFoundError("Fee not found.")
    if (src.payer_type, src.payer_id) != (dst.payer_type, dst.payer_id):
        raise BusinessRuleViolationError("A payment can only be moved to another fee of the same person.")
    if dst.status != AssignmentStatus.ACTIVE:
        raise BusinessRuleViolationError(f"That fee is {dst.status.value.lower()}; nothing can be paid on it.")
    if await credit_is_reversed(session, credit):
        raise BusinessRuleViolationError("This payment was reversed, so there is nothing to move.")
    if await moved_to(session, credit.id) is not None:
        raise BusinessRuleViolationError("This payment was already moved.")
    if (await session.execute(select(PaymentChallan.id).where(PaymentChallan.paid_entry_id == credit.id))).first():
        raise BusinessRuleViolationError("This payment paid a bank challan. Reverse it (it re-opens the challan) and record the money on the right fee instead.")
    other = (await session.execute(select(func.count()).select_from(FeeLedgerEntry).where(
        FeeLedgerEntry.fee_assignment_id == src.id,
        FeeLedgerEntry.entry_type.in_((LedgerEntryType.REFUND, LedgerEntryType.CREDIT_OUT))))).scalar_one()
    if other:
        raise BusinessRuleViolationError("This fee has had a refund or credit moved out, so the payment cannot be moved safely. "
                                         "Reverse it and record the money on the right fee instead.")
    return credit, src, dst


async def _names(session: AsyncSession, *assignments) -> list[str]:
    out = []
    for a in assignments:
        s = await session.get(FeeStructure, a.fee_structure_id)
        out.append(s.name if s else "fee")
    return out


async def request_move(session: AsyncSession, *, credit_id: uuid.UUID, to_assignment_id: uuid.UUID, reason: str, actor_id: uuid.UUID) -> dict:
    reason = " ".join((reason or "").split())
    if len(reason) < 5:
        raise ValidationAppError("Say why the payment is on the wrong fee (for example: tuition paid, entered on hostel by mistake).")
    credit, src, dst = await _checked(session, credit_id, to_assignment_id, lock=False)
    from app.fees.account_service import AccountService

    payer = await AccountService(session).payer(src.payer_type, src.payer_id)
    src_name, dst_name = await _names(session, src, dst)
    who = (payer or {}).get("name") or "payer"
    summary = (f"Move receipt {credit.receipt_number} (Rs. {float(credit.amount):,.0f}, {who}) from {src_name} to {dst_name}")
    payload = {"credit_id": str(credit.id), "to_assignment_id": str(dst.id)}
    r = await gate(session, kind=KIND, actor_id=actor_id, target_id=credit.id, payload=payload, summary=summary, amount=float(credit.amount), reason=reason)
    if r is None:
        fake = ApprovalRequest(kind=KIND, payload=payload, summary=summary, reason=reason, requested_by=actor_id)
        result = await carry_out(session, fake, actor_id)
        return {"pending_approval": False, "message": f"Moved: {summary}.", **result}
    return pending_response(r)


@executor(KIND)
async def carry_out(session: AsyncSession, r: ApprovalRequest, actor_id) -> dict:
    from app.accounts.period import ensure_open
    from app.fees.assignment_service import sync_challans

    pl = r.payload or {}
    credit, src, dst = await _checked(session, uuid.UUID(pl["credit_id"]), uuid.UUID(pl["to_assignment_id"]), lock=True)
    today = institute_today()
    await ensure_open(session, today, "This payment move")
    src_name, dst_name = await _names(session, src, dst)
    now = datetime.now(timezone.utc)
    amount = float(credit.amount)
    out_row = FeeLedgerEntry(fee_assignment_id=src.id, entry_type=LedgerEntryType.CREDIT_OUT, amount=amount, paid_on=credit.paid_on or today,
                             applies_to_entry_id=credit.id, created_by=actor_id, created_at=now,
                             description=f"Receipt {credit.receipt_number} moved to {dst_name} (recorded on the wrong fee)"[:255])
    session.add(out_row)
    await session.flush()
    in_row = FeeLedgerEntry(fee_assignment_id=dst.id, entry_type=LedgerEntryType.CREDIT_IN, amount=amount, paid_on=credit.paid_on or today,
                            related_entry_id=out_row.id, created_by=actor_id, created_at=now,
                            description=f"Receipt {credit.receipt_number} moved from {src_name}"[:255])
    session.add(in_row)
    await session.flush()
    out_row.related_entry_id = in_row.id
    # No fine because of the move: the wrong fee's late fee is paused from the payment day until a week after the move.
    pause_from, pause_until = credit.paid_on or today, today + timedelta(days=PAUSE_DAYS)
    before_pause = (src.late_fee_paused_from, src.late_fee_paused_until)
    if src.late_fee_paused_from is None:
        src.late_fee_paused_from, src.late_fee_paused_until = pause_from, pause_until
    elif src.late_fee_paused_until is not None:          # widen an earlier pause; an open-ended pause is left as it is
        src.late_fee_paused_from = min(src.late_fee_paused_from, pause_from)
        src.late_fee_paused_until = max(src.late_fee_paused_until, pause_until)
    await sync_challans(session, src)
    await sync_challans(session, dst)
    await AuditService(session).log(
        actor_user_id=actor_id, action="fee.payment_moved", entity_type="fee_assignment", entity_id=dst.id,
        before={"fee": str(src.id), "late_fee_pause": [str(x) if x else None for x in before_pause]},
        after={"fee": str(dst.id), "late_fee_pause": [str(src.late_fee_paused_from), str(src.late_fee_paused_until)]},
        metadata={"receipt": credit.receipt_number, "amount": amount, "from": src_name, "to": dst_name,
                  "requested_by": str(r.requested_by) if r.requested_by else None, "reason": r.reason})
    return {"moved": amount, "receipt": credit.receipt_number, "from": src_name, "to": dst_name,
            "late_fee_paused_until": str(src.late_fee_paused_until) if src.late_fee_paused_until else None}
