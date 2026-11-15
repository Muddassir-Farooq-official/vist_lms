"""Fee refunds.

1. REQUEST (accounts office or administration). How much can be refunded:
     credit balance (paid more than is owed)  +  write_off (charges cancelled, for a withdrawal).
   write_off can be at most the charges not already discounted. One open request per fee.
2. APPROVE / REJECT (administrator; never the person who requested it). On approval the
   write-off is applied as an adjustment on the newest instalments first, so the money
   becomes a credit that can be paid back. Amounts are re-checked at approval.
3. PAY (accounts office): method, reference, date -> a REFUND ledger entry with a refund
   voucher number (RFND-<year>-000001). Balances, the student's fee page, the finance
   report and the principal's overview all count it automatically.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fees.batch_terms import shown_term
from app.core.clock import institute_today
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.fees.assignment_service import FeeStatusCalculator, lock_assignment, validate_paid_on
from app.fees.models import AssignmentStatus, FeeAssignment, FeeLedgerEntry, FeeStructure, LedgerEntryType
from app.fees.refund_models import FeeRefund, RefundKind, RefundStatus

OPEN = (RefundStatus.REQUESTED, RefundStatus.APPROVED)


async def next_refund_voucher(session: AsyncSession) -> str:
    from app.core.sequences import next_sequence_value

    year = institute_today().year
    return f"RFND-{year}-{await next_sequence_value(session, f'fee_refund:{year}'):06d}"


class RefundService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _ledger(self, assignment_id: uuid.UUID) -> list[FeeLedgerEntry]:
        return list((await self.session.execute(select(FeeLedgerEntry).where(FeeLedgerEntry.fee_assignment_id == assignment_id))).scalars())

    async def position(self, assignment_id: uuid.UUID) -> dict:
        """What is refundable on this fee right now."""
        a = await self.session.get(FeeAssignment, assignment_id)
        if a is None:
            raise ResourceNotFoundError("Fee not found.")
        entries = await self._ledger(assignment_id)
        rows = FeeStatusCalculator.installments(entries, None)
        net_paid = sum(float(e.amount) for e in entries if e.entry_type in (LedgerEntryType.CREDIT, LedgerEntryType.CREDIT_IN)) - \
            sum(float(e.amount) for e in entries if FeeStatusCalculator._is_reversal(e) or e.entry_type == LedgerEntryType.CREDIT_OUT)
        owed = sum(r["charged"] - r["concession"] for r in rows)
        return {"fee_assignment_id": a.id, "net_paid": round(net_paid, 2), "owed_after_discounts": round(owed, 2),
                "credit_balance": round(max(0.0, net_paid - owed), 2), "max_write_off": round(owed, 2),
                "outstanding": round(sum(r["remaining"] for r in rows), 2)}

    # ---------------------------------------------------------------- 1. request
    async def request(self, *, assignment_id: uuid.UUID, kind: RefundKind, amount: float, write_off: float, reason: str,
                      actor_id: uuid.UUID) -> FeeRefund:
        a = await self.session.get(FeeAssignment, assignment_id)
        if a is None:
            raise ResourceNotFoundError("Fee not found.")
        if a.status != AssignmentStatus.ACTIVE:
            raise BusinessRuleViolationError("Refunds are only for an active fee.")
        if amount <= 0 or write_off < 0:
            raise ValidationAppError("The refund must be more than zero, and the write-off cannot be negative.")
        if len(reason.strip()) < 10:
            raise ValidationAppError("Give the reason (at least 10 characters).")
        if kind == RefundKind.OVERPAYMENT and write_off > 0:
            raise ValidationAppError("An overpayment refund gives back the credit only; use Withdrawal to write charges off.")
        open_ = (await self.session.execute(select(FeeRefund).where(FeeRefund.fee_assignment_id == assignment_id,
                                                                    FeeRefund.status.in_(OPEN)))).scalar_one_or_none()
        if open_:
            raise BusinessRuleViolationError("This fee already has a refund in progress. Finish or reject it first.")
        await self._check_amounts(assignment_id, amount, write_off)
        r = FeeRefund(fee_assignment_id=assignment_id, kind=kind, amount=round(amount, 2), write_off=round(write_off, 2),
                      reason=reason.strip(), requested_by=actor_id)
        self.session.add(r)
        await self.session.flush()
        await self._audit(actor_id, "fee_refund.requested", r, {"amount": amount, "write_off": write_off, "kind": kind.value})
        return r

    async def _check_amounts(self, assignment_id: uuid.UUID, amount: float, write_off: float) -> None:
        """Refundable = what was paid minus what is still owed after the write-off (never below 0).
        E.g. paid 60,000 of 120,000: writing off 10,000 still leaves 110,000 owed, so nothing is refundable;
        to refund 10,000, 70,000 must be written off."""
        p = await self.position(assignment_id)
        if write_off > p["max_write_off"] + 0.005:
            raise BusinessRuleViolationError(f"At most Rs. {p['max_write_off']:,.2f} of charges can be written off on this fee.")
        if amount > p["net_paid"] + 0.005:
            raise BusinessRuleViolationError(f"Only Rs. {p['net_paid']:,.2f} has been paid on this fee.")
        refundable = round(max(0.0, p["net_paid"] - max(0.0, p["owed_after_discounts"] - write_off)), 2)
        if amount > refundable + 0.005:
            need = round(p["owed_after_discounts"] - (p["net_paid"] - amount), 2)
            raise BusinessRuleViolationError(
                f"Rs. {amount:,.2f} cannot be refunded: Rs. {p['net_paid']:,.2f} was paid and Rs. {max(0.0, p['owed_after_discounts'] - write_off):,.2f} "
                f"would still be owed after this write-off, so at most Rs. {refundable:,.2f} can be refunded. "
                f"To refund Rs. {amount:,.2f}, write off Rs. {min(need, p['max_write_off']):,.2f}.",
                details={k: v for k, v in p.items() if k != "fee_assignment_id"} | {"refundable": refundable})

    # ---------------------------------------------------------------- 2. decide
    async def decide(self, refund_id: uuid.UUID, *, approve: bool, note: str | None, actor_id: uuid.UUID) -> FeeRefund:
        r = await self._get(refund_id)
        if r.status != RefundStatus.REQUESTED:
            raise BusinessRuleViolationError("This refund has already been decided.")
        if r.requested_by == actor_id:
            raise AuthorizationError("The person who requested a refund cannot approve or reject it. Ask another administrator.")
        if not approve:
            if not (note or "").strip():
                raise ValidationAppError("Say why the refund is rejected.")
            r.status = RefundStatus.REJECTED
        else:
            await lock_assignment(self.session, r.fee_assignment_id)
            await self._check_amounts(r.fee_assignment_id, float(r.amount), float(r.write_off))
            if float(r.write_off) > 0:
                await self._write_off(r, actor_id)
            r.status = RefundStatus.APPROVED
        r.decided_by, r.decided_at, r.decision_note = actor_id, datetime.now(timezone.utc), (note or "").strip() or None
        await self.session.flush()
        await self._audit(actor_id, "fee_refund.approved" if approve else "fee_refund.rejected", r, {"note": note})
        return r

    async def _write_off(self, r: FeeRefund, actor_id: uuid.UUID) -> None:
        """Cancel `write_off` of the charges, newest instalment first (the part of the term not used)."""
        entries = await self._ledger(r.fee_assignment_id)
        rows = sorted(FeeStatusCalculator.installments(entries, None), key=lambda x: (x["due_date"] or date.min), reverse=True)
        left, now = float(r.write_off), datetime.now(timezone.utc)
        for row in rows:
            room = row["charged"] - row["concession"]
            take = round(min(room, left), 2)
            if take <= 0:
                continue
            self.session.add(FeeLedgerEntry(fee_assignment_id=r.fee_assignment_id, entry_type=LedgerEntryType.CONCESSION, amount=take,
                                            installment_number=row["installment_number"], related_entry_id=row["ledger_entry_id"],
                                            description=f"Withdrawal write-off (refund {str(r.id)[:8]})", created_by=actor_id, created_at=now))
            left = round(left - take, 2)
            if left <= 0:
                break
        await self.session.flush()
        a = await self.session.get(FeeAssignment, r.fee_assignment_id)
        from app.fees.scholarship_service import ScholarshipService

        await ScholarshipService(self.session)._refresh_challans(a, await self.session.get(FeeStructure, a.fee_structure_id))

    # ---------------------------------------------------------------- 2b. cancel (before it is paid)
    async def cancel(self, refund_id: uuid.UUID, *, reason: str, actor_id: uuid.UUID) -> FeeRefund:
        r = await self._get(refund_id)
        if r.status not in (RefundStatus.REQUESTED, RefundStatus.APPROVED):
            raise BusinessRuleViolationError("Only a refund that is still waiting or approved, and not yet paid out, can be cancelled.")
        if len((reason or "").strip()) < 5:
            raise ValidationAppError("Say why the refund is cancelled.")
        if r.status == RefundStatus.APPROVED and float(r.write_off) > 0:
            await self._restore_write_off(r, actor_id)
        r.status = RefundStatus.CANCELLED
        r.decision_note = f"Cancelled: {reason.strip()}"[:500]
        await self.session.flush()
        await self._audit(actor_id, "fee_refund.cancelled", r, {"reason": reason.strip()})
        return r

    async def _restore_write_off(self, r: FeeRefund, actor_id: uuid.UUID) -> None:
        """The ledger is append-only, so a cancelled write-off is undone by a new row linked to it (see
        FeeStatusCalculator._is_concession_reversal); the installment is owed again, with no extra installment."""
        tag = f"Withdrawal write-off (refund {str(r.id)[:8]})"
        entries = await self._ledger(r.fee_assignment_id)
        by_id = {e.id: e for e in entries}
        now = datetime.now(timezone.utc)
        for e in entries:
            if e.entry_type == LedgerEntryType.CONCESSION and (e.description or "") == tag:
                original = by_id.get(e.related_entry_id)
                # Linked to the write-off it undoes (related_entry_id), so it puts that installment's amount back
                # instead of showing as a new installment (audit FIN-18).
                self.session.add(FeeLedgerEntry(fee_assignment_id=r.fee_assignment_id, entry_type=LedgerEntryType.DEBIT, amount=float(e.amount),
                                                installment_number=e.installment_number, due_date=original.due_date if original else None,
                                                related_entry_id=e.id,
                                                description=f"Write-off cancelled (refund {str(r.id)[:8]})", created_by=actor_id, created_at=now))
        await self.session.flush()
        a = await self.session.get(FeeAssignment, r.fee_assignment_id)
        from app.fees.scholarship_service import ScholarshipService

        await ScholarshipService(self.session)._refresh_challans(a, await self.session.get(FeeStructure, a.fee_structure_id))

    # ---------------------------------------------------------------- 3. pay
    async def pay(self, refund_id: uuid.UUID, *, method: str, reference: str | None, paid_on: date | None, actor_id: uuid.UUID) -> FeeRefund:
        r = await self._get(refund_id)
        if r.status != RefundStatus.APPROVED:
            raise BusinessRuleViolationError("Only an approved refund can be paid out.")
        if method != "CASH" and not (reference or "").strip():
            raise ValidationAppError("Give the cheque / transfer reference (needed to match the bank statement).")
        day = validate_paid_on(paid_on)
        from app.accounts.period import ensure_open

        await ensure_open(self.session, day, "This refund")
        await lock_assignment(self.session, r.fee_assignment_id)
        p = await self.position(r.fee_assignment_id)
        if float(r.amount) > p["credit_balance"] + 0.005:
            raise BusinessRuleViolationError(f"Only Rs. {p['credit_balance']:,.2f} is refundable on this fee now (it changed since approval).")
        voucher = await next_refund_voucher(self.session)
        entry = FeeLedgerEntry(fee_assignment_id=r.fee_assignment_id, entry_type=LedgerEntryType.REFUND, amount=float(r.amount),
                               description=f"Refund paid ({r.kind.value.lower()}) — voucher {voucher}", payment_method=method,
                               payment_reference=(reference or "").strip() or None, paid_on=day, receipt_number=voucher,
                               created_by=actor_id, created_at=datetime.now(timezone.utc))
        self.session.add(entry)
        await self.session.flush()
        from app.fees.assignment_service import sync_challans

        await sync_challans(self.session, await self.session.get(FeeAssignment, r.fee_assignment_id))
        r.status, r.paid_by, r.ledger_entry_id, r.voucher_number, r.paid_on = RefundStatus.PAID, actor_id, entry.id, voucher, day
        await self.session.flush()
        await self._audit(actor_id, "fee_refund.paid", r, {"voucher": voucher, "method": method, "reference": reference})
        await self._notify(r)
        return r

    async def _notify(self, r: FeeRefund) -> None:
        from app.fees.models import PayerType
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification
        from app.students.models import Student

        a = await self.session.get(FeeAssignment, r.fee_assignment_id)
        if a.payer_type == PayerType.STUDENT:
            st = await self.session.get(Student, a.payer_id)
            if st and st.user_id:
                await create_notification(self.session, user_id=st.user_id, category=NotificationCategory.FEE, title="Refund paid",
                                          message=f"Rs. {float(r.amount):,.2f} refunded (voucher {r.voucher_number}).", link="/dashboard/student/fees")

    # ---------------------------------------------------------------- helpers
    async def _get(self, refund_id: uuid.UUID) -> FeeRefund:
        r = await self.session.get(FeeRefund, refund_id)
        if r is None:
            raise ResourceNotFoundError("Refund not found.")
        return r

    async def _audit(self, actor_id, action, r: FeeRefund, extra: dict) -> None:
        from app.audit.service import AuditService

        await AuditService(self.session).log(actor_user_id=actor_id, action=action, entity_type="fee_refund", entity_id=r.id,
                                             after={"fee_assignment_id": str(r.fee_assignment_id), "status": r.status.value, **extra})

    async def listing(self, status: RefundStatus | None = None) -> list[dict]:
        from app.fees.account_service import AccountService

        stmt = select(FeeRefund, FeeAssignment, FeeStructure).join(FeeAssignment, FeeAssignment.id == FeeRefund.fee_assignment_id) \
            .join(FeeStructure, FeeStructure.id == FeeAssignment.fee_structure_id).order_by(FeeRefund.requested_at.desc())
        if status:
            stmt = stmt.where(FeeRefund.status == status)
        acc = AccountService(self.session)
        out = []
        for r, a, s in (await self.session.execute(stmt)).all():
            payer = await acc.payer(a.payer_type, a.payer_id)
            out.append({"id": r.id, "fee_assignment_id": a.id, "fee": s.name, "term_label": shown_term(a, s), "payer": payer,
                        "kind": r.kind.value, "amount": float(r.amount), "write_off": float(r.write_off), "reason": r.reason,
                        "status": r.status.value, "requested_by": r.requested_by, "requested_at": r.requested_at,
                        "decided_at": r.decided_at, "decision_note": r.decision_note, "voucher_number": r.voucher_number,
                        "paid_on": r.paid_on, "ledger_entry_id": r.ledger_entry_id})
        return out