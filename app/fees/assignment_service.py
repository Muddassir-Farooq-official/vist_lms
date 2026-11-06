from __future__ import annotations

from app.core.clock import institute_today

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.employees.models import Employee
from app.fees.models import AssignmentStatus, FeeAssignment, FeeLedgerEntry, FeeStructure, LedgerEntryType, PayerType
from app.fees.payment_gateway import get_payment_gateway
from app.fees.payment_models import ChallanStatus, PaymentChallan
from app.fees.repository import ChallanRepository, FeeAssignmentRepository, FeeStructureRepository
from app.fees.schemas import RecordPaymentRequest
from app.notifications.models import NotificationCategory
from app.notifications.service import create_notification
from app.students.models import Student


async def _resolve_payer_user_id(session: AsyncSession, payer_type: PayerType, payer_id: uuid.UUID) -> uuid.UUID | None:
    """Same lookup as app/fees/service.py's copy — kept local rather
    than imported to avoid a cross-import between the two fee service
    modules for one three-line helper."""
    if payer_type == PayerType.STUDENT:
        student = await session.get(Student, payer_id)
        return student.user_id if student else None
    employee = await session.get(Employee, payer_id)
    return employee.user_id if employee else None


class FeeStatusCalculator:
    """Computes what's owed from the append-only ledger — never a cached
    balance. Fine calculation uses FIFO allocation: payments apply to the
    oldest unpaid installment first, and each installment still unpaid
    past its due date accrues late_fee_per_day independently for every
    day late — multiple simultaneously-overdue installments each accrue
    their own daily fine rather than one shared fine (ASSUMPTION,
    flagged: 'per day 200 rupee charge' didn't specify per-installment vs
    per-assignment stacking, this is the interpretation implemented)."""

    # A staff "mark unpaid" writes a DEBIT that re-opens an installment
    # (see ChallanService.mark_unpaid). It is not a new charge: it cancels
    # an earlier payment. Recognised by this description prefix.
    REVERSAL_PREFIX = "Payment reversed"

    @staticmethod
    def _is_reversal(e: FeeLedgerEntry) -> bool:
        """Money taken back off the payments: a staff "mark unpaid" reversal, or a refund paid out."""
        return e.entry_type == LedgerEntryType.REFUND or (
            e.entry_type == LedgerEntryType.DEBIT and (e.description or "").startswith(FeeStatusCalculator.REVERSAL_PREFIX))

    @staticmethod
    def _is_concession_reversal(e: FeeLedgerEntry, concession_ids: set) -> bool:
        """A cancelled write-off / discount: a DEBIT linked to the CONCESSION it undoes. It restores what is owed on
        that installment; it is not a new installment."""
        return e.entry_type == LedgerEntryType.DEBIT and getattr(e, "related_entry_id", None) in concession_ids

    @staticmethod
    def _late_days(start: date, end: date, pause: tuple | None) -> int:
        """Days from start to end, less the days the late fee was paused (student suspended or left)."""
        days = (end - start).days
        if days <= 0:
            return 0
        if pause and pause[0]:
            p_from, p_until = pause[0], pause[1] or end
            overlap = (min(end, p_until) - max(start, p_from)).days
            if overlap > 0:
                days -= overlap
        return max(days, 0)

    @staticmethod
    def rate_for_day(day_index: int, rate: float) -> float:
        """The daily late fee on the n-th late day (0 = the first). With "double every N days" set, the rate doubles
        after each N late days, up to the highest daily rate if one is set."""
        every = int(getattr(settings, "fee_late_fee_double_every_days", 0) or 0)
        top = float(getattr(settings, "fee_late_fee_max_per_day", 0) or 0)
        r = rate * (2 ** min(day_index // every, 40)) if every > 0 else rate
        return min(r, top) if top > 0 else r

    @staticmethod
    def late_amount(days: int, rate: float) -> float:
        """Total late fee for this many late days: flat, or doubling in steps (see rate_for_day)."""
        if days <= 0 or not rate:
            return 0.0
        every = int(getattr(settings, "fee_late_fee_double_every_days", 0) or 0)
        if every <= 0:
            top = float(getattr(settings, "fee_late_fee_max_per_day", 0) or 0)
            return days * (min(rate, top) if top > 0 else rate)
        total, step = 0.0, 0
        while days > 0:
            n = min(every, days)
            total += n * FeeStatusCalculator.rate_for_day(step * every, rate)
            days -= n
            step += 1
        return total

    @staticmethod
    def installments(entries: list[FeeLedgerEntry], late_fee_per_day: float | None, *, as_of: date | None = None,
                     pause: tuple | None = None) -> list[dict]:
        """One row per installment, worked out from the append-only ledger.

        - charged: the installment's DEBIT amount
        - concession: CONCESSION entries linked to it (related_entry_id)
        - paid: payments applied oldest-installment-first (FIFO); a payment
          reversal cancels the most recent payments first
        - remaining = charged - concession - paid
        - status: PAID | OVERDUE | PARTIAL | UPCOMING
        - late_fee: days late x late_fee_per_day on whatever was unpaid
          past the due date (to today, or to the day it was settled)
        """
        today = as_of or institute_today()
        concessions = {e.id: e for e in entries if e.entry_type == LedgerEntryType.CONCESSION}
        debits = sorted(
            [e for e in entries if e.entry_type == LedgerEntryType.DEBIT and not FeeStatusCalculator._is_reversal(e)
             and not FeeStatusCalculator._is_concession_reversal(e, set(concessions))],
            key=lambda e: (e.due_date or date.min, e.installment_number or 0),
        )
        credits = sorted([e for e in entries if e.entry_type in (LedgerEntryType.CREDIT, LedgerEntryType.CREDIT_IN)],
                         key=lambda e: (getattr(e, "paid_on", None) or (e.created_at.date() if e.created_at else date.min), e.created_at or datetime.min.replace(tzinfo=timezone.utc)))

        # A payment counts from the day it was actually PAID (paid_on), not the
        # day staff typed it in; older rows without paid_on use created_at.
        def _paid_day(c):
            return c.paid_on if getattr(c, "paid_on", None) else (c.created_at.date() if c.created_at else today)
        pool: list[list] = [[float(c.amount), _paid_day(c), c.id, getattr(c, "applies_to_entry_id", None)] for c in credits]
        by_credit = {p[2]: p for p in pool}

        # Reversals: one linked to a payment (related_entry_id) cancels THAT payment; refunds and older
        # unlinked reversals come off the newest payments first.
        left = 0.0
        for e in entries:
            if e.entry_type == LedgerEntryType.CREDIT_OUT:          # advance credit moved to another fee
                left += float(e.amount)
                continue
            if not FeeStatusCalculator._is_reversal(e):
                continue
            target = by_credit.get(getattr(e, "related_entry_id", None)) if e.entry_type == LedgerEntryType.DEBIT else None
            if target is not None:
                take = min(target[0], float(e.amount))
                target[0] -= take
                left += float(e.amount) - take
            else:
                left += float(e.amount)
        for item in reversed(pool):
            if left <= 1e-9:
                break
            take = min(item[0], left)
            item[0] -= take
            left -= take

        concession_by_debit: dict = {}
        for e in entries:
            if e.entry_type == LedgerEntryType.CONCESSION and e.related_entry_id is not None:
                concession_by_debit[e.related_entry_id] = concession_by_debit.get(e.related_entry_id, 0.0) + float(e.amount)
        for e in entries:
            if FeeStatusCalculator._is_concession_reversal(e, set(concessions)):
                target = concessions[e.related_entry_id].related_entry_id
                if target is not None:
                    concession_by_debit[target] = max(concession_by_debit.get(target, 0.0) - float(e.amount), 0.0)

        owed_by: dict = {}
        paid_by: dict = {d.id: 0.0 for d in debits}
        settled_by: dict = {}
        paid_from_by: dict = {d.id: [] for d in debits}       # which payments settled each installment, and how much of each
        for d in debits:
            charged = float(d.amount)
            owed_by[d.id] = charged - min(concession_by_debit.get(d.id, 0.0), charged)

        def _apply(d_id, item) -> None:
            take = min(owed_by[d_id] - paid_by[d_id], item[0])
            if take <= 1e-9:
                return
            paid_by[d_id] += take
            item[0] -= take
            paid_from_by[d_id].append({"ledger_entry_id": item[2], "amount": round(take, 2)})
            if owed_by[d_id] - paid_by[d_id] <= 1e-9:
                settled_by[d_id] = max(settled_by.get(d_id, item[1]), item[1])

        # 1. A payment made against an installment's own challan settles that installment first.
        for item in pool:
            if item[3] is not None and item[3] in owed_by and item[0] > 1e-9:
                _apply(item[3], item)
        # 2. Everything else: oldest installment first, oldest payment first.
        idx = 0
        live = [p for p in pool if p[0] > 1e-9]
        for d in debits:
            while owed_by[d.id] - paid_by[d.id] > 1e-9 and idx < len(live):
                _apply(d.id, live[idx])
                if live[idx][0] <= 1e-9:
                    idx += 1

        rows: list[dict] = []
        for d in debits:
            charged = float(d.amount)
            concession = min(concession_by_debit.get(d.id, 0.0), charged)
            owed = owed_by[d.id]
            paid = paid_by[d.id]
            settled_at = settled_by.get(d.id)
            paid_from = paid_from_by[d.id]
            remaining = round(max(owed - paid, 0.0), 2)
            due = d.due_date or today
            # An instalment created after its own due date (fee activated late,
            # previous dues imported) only accrues a late fee from a grace date
            # after it was created, never retroactively from the old due date.
            late_from = due
            created_day = d.created_at.date() if getattr(d, "created_at", None) else None
            if created_day and created_day > due:
                late_from = created_day + timedelta(days=settings.fee_backdated_grace_days)
            grace = int(getattr(settings, "fee_late_fee_grace_days", 0) or 0)
            if grace:
                late_from = max(late_from, due + timedelta(days=grace))     # no late fee during the grace days after the due date
            late_fee = 0.0
            rate_now, rises_on = None, None
            if late_fee_per_day:
                if remaining > 0.005 and late_from < today:
                    days_late = FeeStatusCalculator._late_days(late_from, today, pause)
                    late_fee = FeeStatusCalculator.late_amount(days_late, late_fee_per_day)
                    rate_now = FeeStatusCalculator.rate_for_day(days_late, late_fee_per_day)      # what tomorrow's late day costs
                    every = int(getattr(settings, "fee_late_fee_double_every_days", 0) or 0)
                    if every > 0 and FeeStatusCalculator.rate_for_day(days_late + every, late_fee_per_day) > rate_now:
                        rises_on = today + timedelta(days=every - days_late % every)
                elif remaining <= 0.005 and owed > 1e-9:
                    settled_on = settled_at if settled_at else today
                    if settled_on > late_from:
                        late_fee = FeeStatusCalculator.late_amount(FeeStatusCalculator._late_days(late_from, settled_on, pause), late_fee_per_day)
            cap = float(settings.fee_late_fee_cap or 0)
            if cap > 0:
                late_fee = min(late_fee, cap)          # one instalment can never build up more than the cap
            if remaining <= 0.005:
                status = "PAID"
            elif due < today:
                status = "OVERDUE"
            elif paid > 0.005:
                status = "PARTIAL"
            else:
                status = "UPCOMING"
            rows.append({
                "ledger_entry_id": d.id, "installment_number": d.installment_number, "due_date": d.due_date,
                "description": d.description, "charged": round(charged, 2), "concession": round(concession, 2),
                "paid": round(paid, 2), "remaining": remaining, "status": status, "late_fee": round(late_fee, 2),
                "late_fee_rate_now": round(rate_now, 2) if rate_now else None, "late_fee_rate_rises_on": rises_on,
                "late_fee_posted": round(float(d.late_fee_charged_amount or 0), 2),
                "paid_from": paid_from,
            })
        return rows

    @staticmethod
    def compute(entries: list[FeeLedgerEntry], late_fee_per_day: float | None, *, as_of: date | None = None, pause: tuple | None = None) -> dict:
        today = as_of or institute_today()
        rows = FeeStatusCalculator.installments(entries, late_fee_per_day, as_of=today, pause=pause)
        total_charged = sum(r["charged"] for r in rows)
        reversed_total = sum(float(e.amount) for e in entries if FeeStatusCalculator._is_reversal(e))
        total_paid = (sum(float(e.amount) for e in entries if e.entry_type in (LedgerEntryType.CREDIT, LedgerEntryType.CREDIT_IN))
                      - reversed_total - sum(float(e.amount) for e in entries if e.entry_type == LedgerEntryType.CREDIT_OUT))
        overdue_principal = round(sum(r["remaining"] for r in rows if r["status"] == "OVERDUE"), 2)
        accrued_fine = round(sum(r["late_fee"] for r in rows), 2)
        total_concession = sum(r["concession"] for r in rows)
        return {
            "total_charged": round(total_charged, 2),
            "total_concession": round(total_concession, 2),
            "total_payable": round(total_charged - total_concession, 2),     # what is owed after scholarships and write-offs
            "total_remaining": round(sum(r["remaining"] for r in rows), 2),  # what is still to be paid (due now or later)
            "total_paid": round(total_paid, 2),
            # Paid beyond what this fee needs: advance credit, usable on another fee or refundable.
            "credit": round(max(total_paid - (total_charged - total_concession - sum(r["remaining"] for r in rows)), 0.0), 2),
            "overdue_principal": overdue_principal,
            "accrued_fine": accrued_fine,
            # A late fee on an instalment that has since been paid can still be posted, but it no longer keeps the payer "owing".
            "total_due_now": round(overdue_principal + sum(r["late_fee"] for r in rows if r["remaining"] > 0.005), 2),
        }

    @staticmethod
    def compute_fine_for_debit(entries: list[FeeLedgerEntry], debit_id: uuid.UUID, late_fee_per_day: float | None, *, as_of: date | None = None,
                               pause: tuple | None = None) -> float:
        """The currently-calculated late fee for ONE installment (what
        FeeAssignmentService.post_late_fee posts). Same FIFO settlement as
        installments(), because whether a debit is settled, and when,
        depends on payment order across the whole assignment."""
        for r in FeeStatusCalculator.installments(entries, late_fee_per_day, as_of=as_of, pause=pause):
            if r["ledger_entry_id"] == debit_id:
                return r["late_fee"]
        return 0.0


async def next_receipt_number(session: AsyncSession, *, year: int | None = None) -> str:
    """RCPT-<year>-<6 digits>, one institute-wide sequence per calendar year."""
    from app.core.sequences import next_sequence_value

    year = year or institute_today().year
    return f"RCPT-{year}-{await next_sequence_value(session, f'fee_receipt:{year}'):06d}"


async def lock_assignment(session: AsyncSession, assignment_id: uuid.UUID) -> FeeAssignment:
    """Lock one fee's row until this transaction ends. Every write that changes what is owed or paid on a fee takes
    this lock first, so two cashiers (or a cashier and the bank) working on the same fee at once are served one
    after the other and the second one sees the first one's payment."""
    a = (await session.execute(select(FeeAssignment).where(FeeAssignment.id == assignment_id).with_for_update()
                               .execution_options(populate_existing=True))).scalar_one_or_none()
    if a is None:
        raise ResourceNotFoundError("Fee assignment not found.")
    return a


def late_pause(assignment) -> tuple | None:
    """(paused_from, paused_until) of a fee's late fee, or None. Pass as `pause=` to FeeStatusCalculator."""
    start = getattr(assignment, "late_fee_paused_from", None)
    return (start, getattr(assignment, "late_fee_paused_until", None)) if start else None


async def _late_fee_per_day(session: AsyncSession, assignment: FeeAssignment) -> float | None:
    from app.fees.models import FeeStructure

    structure = await session.get(FeeStructure, assignment.fee_structure_id)
    return float(structure.late_fee_per_day) if structure is not None and structure.late_fee_per_day else None


async def outstanding_on(session: AsyncSession, assignment: FeeAssignment) -> float:
    entries = await FeeAssignmentRepository(session).get_ledger(assignment.id)
    rows = FeeStatusCalculator.installments(entries, await _late_fee_per_day(session, assignment), pause=late_pause(assignment))
    return round(sum(r["remaining"] for r in rows), 2)


async def sync_challans(session: AsyncSession, assignment: FeeAssignment) -> None:
    """Keep every unpaid challan of a fee in step with its installment: it shows exactly what is still due on that
    installment, it is cancelled once nothing is due (or the fee is waived or cancelled), and it comes back if the
    installment is owed again (a payment reversed, a fee made active again). Paid challans are never touched."""
    await session.flush()
    entries = await FeeAssignmentRepository(session).get_ledger(assignment.id)
    rows = {r["ledger_entry_id"]: r for r in FeeStatusCalculator.installments(entries, await _late_fee_per_day(session, assignment), pause=late_pause(assignment))}
    challans = (await session.execute(select(PaymentChallan).where(PaymentChallan.fee_assignment_id == assignment.id))).scalars().all()
    for ch in challans:
        if ch.status == ChallanStatus.PAID:
            continue
        r = rows.get(ch.fee_ledger_entry_id)
        if assignment.status != AssignmentStatus.ACTIVE or r is None or r["remaining"] <= 0.005:
            if ch.status == ChallanStatus.PENDING:
                ch.status = ChallanStatus.CANCELLED
            continue
        ch.amount = r["remaining"]
        if ch.status == ChallanStatus.CANCELLED:
            ch.status = ChallanStatus.PENDING


def _mentions_challan(e: FeeLedgerEntry, consumer_number: str) -> bool:
    return bool(consumer_number) and consumer_number in (e.description or "")


async def credit_is_reversed(session: AsyncSession, credit: FeeLedgerEntry) -> bool:
    """True when this payment has already been taken back.

    A reversal written by this version always points at its payment (related_entry_id). Older "mark unpaid"
    reversals did not: they only name the challan's consumer number in their description. For those, the n-th
    such reversal of a challan cancels the n-th payment of that challan."""
    linked = (await session.execute(select(func.count()).select_from(FeeLedgerEntry).where(
        FeeLedgerEntry.related_entry_id == credit.id, FeeLedgerEntry.entry_type == LedgerEntryType.DEBIT,
        FeeLedgerEntry.description.like(FeeStatusCalculator.REVERSAL_PREFIX + "%")))).scalar_one()
    if linked:
        return True
    challan = (await session.execute(select(PaymentChallan).where(PaymentChallan.fee_assignment_id == credit.fee_assignment_id))).scalars().all()
    number = next((c.consumer_number for c in challan if _mentions_challan(credit, c.consumer_number)), None)
    if number is None:
        return False
    entries = await FeeAssignmentRepository(session).get_ledger(credit.fee_assignment_id)
    payments = sorted([e for e in entries if e.entry_type == LedgerEntryType.CREDIT and _mentions_challan(e, number)], key=lambda e: e.created_at)
    old_reversals = [e for e in entries if e.entry_type == LedgerEntryType.DEBIT and e.related_entry_id is None
                     and (e.description or "").startswith(FeeStatusCalculator.REVERSAL_PREFIX) and _mentions_challan(e, number)]
    position = next((i for i, p in enumerate(payments) if p.id == credit.id), None)
    return position is not None and position < len(old_reversals)


async def _challan_credit(session: AsyncSession, challan: PaymentChallan) -> FeeLedgerEntry | None:
    """The payment that paid this challan: the recorded link, or (older rows) the newest payment naming its consumer number."""
    if challan.paid_entry_id is not None:
        return await session.get(FeeLedgerEntry, challan.paid_entry_id)
    entries = await FeeAssignmentRepository(session).get_ledger(challan.fee_assignment_id)
    named = [e for e in entries if e.entry_type == LedgerEntryType.CREDIT and _mentions_challan(e, challan.consumer_number)]
    return max(named, key=lambda e: e.created_at) if named else None


def validate_paid_on(paid_on: date | None) -> date:
    today = institute_today()
    day = paid_on or today
    if day > today:
        raise BusinessRuleViolationError("The payment date cannot be in the future.")
    if (today - day).days > settings.fee_payment_max_backdate_days:
        raise BusinessRuleViolationError(f"The payment date is more than {settings.fee_payment_max_backdate_days} days ago. Check the date, or ask the administrator.")
    return day


async def post_settled_late_fees(session: AsyncSession, assignment: FeeAssignment, actor_id: uuid.UUID | None) -> float:
    """After a payment: every instalment of this fee that is now paid in full but still has a late fee not yet posted gets
    that late fee posted as a Fine (Fines section, collectible, on the student's account). Uses the same posting as the
    "Post late fee" button, so nothing is ever charged twice. Never stops a payment: a problem is only logged.
    Off when Settings -> Rules -> Fees -> "add it to Fines when a late instalment is paid" is off."""
    if not int(getattr(settings, "fee_late_fee_auto_post", 1) or 0):
        return 0.0
    from app.core.logging import get_logger

    try:
        async with session.begin_nested():
            structure = await session.get(FeeStructure, assignment.fee_structure_id)
            if structure is None or not structure.late_fee_per_day:
                return 0.0
            entries = await FeeAssignmentRepository(session).get_ledger(assignment.id)
            rows = FeeStatusCalculator.installments(entries, float(structure.late_fee_per_day), pause=late_pause(assignment))
            svc = FeeAssignmentService(session)
            total = 0.0
            for r in rows:
                if r["remaining"] <= 0.005 and r["late_fee"] > float(r.get("late_fee_posted") or 0) + 0.005:
                    fine = await svc.post_late_fee(r["ledger_entry_id"], actor_id=actor_id)
                    total += float(fine.amount) if fine is not None else 0.0
            return round(total, 2)
    except Exception as exc:  # noqa: BLE001 - the payment itself must always go through
        get_logger(__name__).warning("late_fee_auto_post_failed", assignment=str(assignment.id), error=str(exc)[:300])
        return 0.0


class FeeAssignmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = FeeAssignmentRepository(session)
        self.structure_repo = FeeStructureRepository(session)
        self.audit = AuditService(session)

    async def get(self, assignment_id: uuid.UUID) -> FeeAssignment:
        assignment = await self.repo.get_by_id(assignment_id)
        if assignment is None:
            raise ResourceNotFoundError("Fee assignment not found.")
        return assignment

    async def resolve_payer_name(self, payer_type: PayerType, payer_id: uuid.UUID) -> str:
        if payer_type == PayerType.STUDENT:
            student = await self.session.get(Student, payer_id)
            return f"{student.first_name} {student.last_name}" if student else "Unknown student"
        employee = await self.session.get(Employee, payer_id)
        return f"{employee.first_name} {employee.last_name}" if employee else "Unknown employee"

    async def get_status(self, assignment: FeeAssignment) -> dict:
        entries = await self.repo.get_ledger(assignment.id)
        structure = await self.structure_repo.get_by_id(assignment.fee_structure_id)
        late_fee = float(structure.late_fee_per_day) if structure and structure.late_fee_per_day else None
        return FeeStatusCalculator.compute(entries, late_fee, pause=late_pause(assignment))

    async def record_payment(
        self, assignment_id: uuid.UUID, data: RecordPaymentRequest, *, actor_id: uuid.UUID
    ) -> FeeLedgerEntry:
        await self.get(assignment_id)
        assignment = await lock_assignment(self.session, assignment_id)      # a second cashier on this fee waits here
        if assignment.status != AssignmentStatus.ACTIVE:
            raise BusinessRuleViolationError(
                f"Cannot record a payment against a {assignment.status.value.lower()} assignment."
            )
        paid_on = validate_paid_on(data.paid_on)
        from app.accounts.period import ensure_open
        from app.fees.references import refuse_reused_reference

        await ensure_open(self.session, paid_on, "This payment")
        await refuse_reused_reference(self.session, data.reference, data.method.value)

        # Never take more than is owed: an overpayment used to be accepted
        # silently (Rs 500,000 against a Rs 120,000 fee) and then vanished from
        # every balance. Refunds/advances are handled outside this screen.
        structure = await self.structure_repo.get_by_id(assignment.fee_structure_id)
        rows = FeeStatusCalculator.installments(
            await self.repo.get_ledger(assignment.id), float(structure.late_fee_per_day) if structure and structure.late_fee_per_day else None,
            pause=late_pause(assignment))
        outstanding = round(sum(r["remaining"] for r in rows), 2)
        if outstanding <= 0.005:
            raise BusinessRuleViolationError("Nothing is owed on this fee; it is already fully paid.")
        if data.amount > outstanding + 0.005:
            raise BusinessRuleViolationError(
                f"Rs. {data.amount:,.2f} is more than what is still owed on this fee (Rs. {outstanding:,.2f}). "
                "Record at most the outstanding amount.", details={"outstanding": outstanding})

        entry = FeeLedgerEntry(
            fee_assignment_id=assignment.id,
            entry_type=LedgerEntryType.CREDIT,
            amount=data.amount,
            installment_number=None,
            due_date=None,
            description=data.description or "Payment received",
            payment_method=data.method.value,
            payment_reference=data.reference,
            paid_on=paid_on,
            receipt_number=await next_receipt_number(self.session),
            created_by=actor_id,
            created_at=datetime.now(timezone.utc),
        )
        await self.repo.add_ledger_entry(entry)
        await sync_challans(self.session, assignment)        # challans of installments this payment settled stop being payable
        self.late_fee_posted = await post_settled_late_fees(self.session, assignment, actor_id)

        await self.audit.log(
            actor_user_id=actor_id,
            action="fee.payment_recorded",
            entity_type="fee_assignment",
            entity_id=assignment.id,
            after={"amount": data.amount, "method": data.method.value, "reference": data.reference,
                   "paid_on": str(paid_on), "receipt_number": entry.receipt_number},
        )

        payer_user_id = await _resolve_payer_user_id(self.session, assignment.payer_type, assignment.payer_id)
        if payer_user_id is not None:
            await create_notification(
                self.session, user_id=payer_user_id, category=NotificationCategory.FEE,
                title="A payment has been recorded on your account",
                message=f"Rs. {data.amount:.2f} — {entry.description} — receipt {entry.receipt_number}",
                link="/dashboard/student" if assignment.payer_type == PayerType.STUDENT else "/dashboard/faculty",
            )

        return entry

    async def post_late_fee(self, ledger_entry_id: uuid.UUID, *, actor_id: uuid.UUID):
        """The actual fix for 'late fees were calculate-only' — posts
        whatever portion of this ONE installment's currently-calculated
        fine hasn't already been posted, as a real Fine in the Fines
        module (its own proven issue/pay/waive lifecycle — deliberately
        not a second parallel ledger inside Fees). Safe to call
        repeatedly (e.g. an accounts officer running this weekly): each
        call only posts the NEW amount accrued since the last call,
        tracked via late_fee_charged_amount on the debit itself, so
        nobody is ever double-charged for the same days late.
        Returns None if there's nothing new to post (nothing overdue,
        or already fully posted for this debit)."""
        debit = await self.session.get(FeeLedgerEntry, ledger_entry_id)
        if debit is None or debit.entry_type != LedgerEntryType.DEBIT:
            raise ResourceNotFoundError("Installment (debit ledger entry) not found.")

        assignment = await self.get(debit.fee_assignment_id)
        structure = await self.structure_repo.get_by_id(assignment.fee_structure_id)
        if structure is None or not structure.late_fee_per_day:
            raise BusinessRuleViolationError("This fee structure has no late fee configured.")

        entries = await self.repo.get_ledger(assignment.id)
        current_total_fine = FeeStatusCalculator.compute_fine_for_debit(
            entries, debit.id, float(structure.late_fee_per_day), pause=late_pause(assignment)
        )
        already_charged = float(debit.late_fee_charged_amount or 0)
        new_amount = round(current_total_fine - already_charged, 2)

        if new_amount <= 0:
            return None

        from app.fines.models import FinePayerType, FineSource
        from app.fines.service import FineService

        fine_payer_type = FinePayerType.STUDENT if assignment.payer_type == PayerType.STUDENT else FinePayerType.EMPLOYEE
        fine = await FineService(self.session).issue(
            payer_type=fine_payer_type,
            payer_id=assignment.payer_id,
            amount=new_amount,
            reason=f"Late payment fee \u2014 installment {debit.installment_number or ''} of {structure.name} (due {debit.due_date})".strip(),
            source=FineSource.FEE_LATE_PAYMENT,
            actor_id=actor_id,
            related_entity_type="fee_ledger_entry",
            related_entity_id=debit.id,
        )

        debit.late_fee_charged_amount = current_total_fine

        await self.audit.log(
            actor_user_id=actor_id,
            action="fee.late_fee_posted",
            entity_type="fee_ledger_entry",
            entity_id=debit.id,
            after={"amount_posted": new_amount, "fine_id": str(fine.id)},
        )
        return fine

    async def change_due_date(self, ledger_entry_id: uuid.UUID, *, new_date: date, reason: str, actor_id: uuid.UUID) -> dict:
        """Move one instalment's due date (e.g. an extension for a student, or the whole class). Only an instalment that is
        still owed, only to today or later (never backwards into the past, which would charge late fees for days that
        were not late), always with a reason. The ledger amounts are not touched; the instalment's open challan gets the
        new date; the old and new dates are kept in the audit log. A late fee that was already posted as a fine is not
        changed here (waive it under Fines if it should no longer be charged)."""
        reason = (reason or "").strip()
        if len(reason) < 4:
            raise ValidationAppError("Write the reason for changing the due date (it is kept on record).")
        today = institute_today()
        if new_date < today:
            raise ValidationAppError("The new due date must be today or later.")
        if new_date > today + timedelta(days=730):
            raise ValidationAppError("The new due date must be within two years.")
        debit = await self.session.get(FeeLedgerEntry, ledger_entry_id)
        if debit is None or debit.entry_type != LedgerEntryType.DEBIT or FeeStatusCalculator._is_reversal(debit):
            raise ResourceNotFoundError("Instalment not found.")
        assignment = await lock_assignment(self.session, debit.fee_assignment_id)
        if assignment.status != AssignmentStatus.ACTIVE:
            raise BusinessRuleViolationError("This fee is not active, so its due dates cannot be changed.")
        entries = await self.repo.get_ledger(assignment.id)
        row = next((r for r in FeeStatusCalculator.installments(entries, await _late_fee_per_day(self.session, assignment), pause=late_pause(assignment))
                    if r["ledger_entry_id"] == debit.id), None)
        if row is None:
            raise ResourceNotFoundError("Instalment not found.")
        if row["remaining"] <= 0.005:
            raise BusinessRuleViolationError("This instalment is already paid in full.")
        old = debit.due_date
        if old == new_date:
            raise ValidationAppError("That is already its due date.")
        debit.due_date = new_date
        challans = (await self.session.execute(select(PaymentChallan).where(
            PaymentChallan.fee_ledger_entry_id == debit.id, PaymentChallan.status.in_((ChallanStatus.PENDING, ChallanStatus.EXPIRED))))).scalars().all()
        for ch in challans:
            ch.due_date = new_date
            if ch.status == ChallanStatus.EXPIRED:
                ch.status = ChallanStatus.PENDING          # payable again until the new date
        posted = round(float(debit.late_fee_charged_amount or 0), 2)
        await self.audit.log(actor_user_id=actor_id, action="fee.due_date_changed", entity_type="fee_ledger_entry", entity_id=debit.id,
                             before={"due_date": str(old) if old else None},
                             after={"due_date": str(new_date), "reason": reason[:300], "assignment_id": str(assignment.id),
                                    "remaining": row["remaining"], "late_fee_before": row["late_fee"], "late_fee_already_posted": posted})
        payer_user_id = await _resolve_payer_user_id(self.session, assignment.payer_type, assignment.payer_id)
        if payer_user_id is not None:
            await create_notification(
                self.session, user_id=payer_user_id, category=NotificationCategory.FEE, title="Fee due date changed",
                message=(f"{debit.description or 'Instalment ' + str(debit.installment_number or '')}: now due on {new_date:%d %b %Y}"
                         + (f" (was {old:%d %b %Y})" if old else "") + f". Rs. {row['remaining']:,.0f} to pay."),
            )
        await self.session.flush()
        return {"ledger_entry_id": debit.id, "assignment_id": assignment.id, "old_due_date": old, "new_due_date": new_date,
                "remaining": row["remaining"], "late_fee_already_posted": posted}

    async def change_due_date_for_structure(self, structure_id: uuid.UUID, installment_number: int, *, new_date: date, reason: str,
                                            actor_id: uuid.UUID) -> dict:
        """The same change for one instalment of a fee for everyone on it (e.g. "2nd instalment of Fall 2026 tuition now
        due 30 Nov"). Instalments already paid in full are left as they are and counted."""
        structure = await self.structure_repo.get_by_id(structure_id)
        if structure is None:
            raise ResourceNotFoundError("Fee structure not found.")
        if not 1 <= installment_number <= int(structure.installment_count or 1):
            raise ValidationAppError(f"This fee has instalments 1 to {structure.installment_count}.")
        if len((reason or "").strip()) < 4:
            raise ValidationAppError("Write the reason for changing the due date (it is kept on record).")
        if new_date < institute_today():
            raise ValidationAppError("The new due date must be today or later.")
        debits = (await self.session.execute(
            select(FeeLedgerEntry).join(FeeAssignment, FeeAssignment.id == FeeLedgerEntry.fee_assignment_id)
            .where(FeeAssignment.fee_structure_id == structure_id, FeeAssignment.status == AssignmentStatus.ACTIVE,
                   FeeLedgerEntry.entry_type == LedgerEntryType.DEBIT, FeeLedgerEntry.installment_number == installment_number)
            .order_by(FeeLedgerEntry.created_at))).scalars().all()
        changed = paid = same = other = posted = 0
        for d in debits:
            if FeeStatusCalculator._is_reversal(d):
                continue
            if d.due_date == new_date:
                same += 1
                continue
            try:
                r = await self.change_due_date(d.id, new_date=new_date, reason=reason, actor_id=actor_id)
            except BusinessRuleViolationError as exc:
                if "paid in full" in str(exc):
                    paid += 1
                else:
                    other += 1
                continue
            changed += 1
            posted += 1 if r["late_fee_already_posted"] > 0 else 0
        await self.audit.log(actor_user_id=actor_id, action="fee.due_date_changed_for_all", entity_type="fee_structure", entity_id=structure.id,
                             after={"installment_number": installment_number, "due_date": str(new_date), "reason": reason.strip()[:300],
                                    "changed": changed, "already_paid": paid, "already_on_that_date": same, "skipped": other})
        return {"changed": changed, "already_paid": paid, "already_on_that_date": same, "skipped": other, "with_late_fee_already_posted": posted}

    async def change_status(
        self, assignment_id: uuid.UUID, *, status: AssignmentStatus, actor_id: uuid.UUID, reason: str | None = None, approved: bool = False
    ):
        """Waive, cancel or re-activate one person's fee. Always with a reason (kept in the audit log). A waived or
        cancelled fee's challans stop working at once; making it active again brings back the ones still owed.
        Money already paid on it stays recorded (a cancelled fee with payments can then be refunded)."""
        await self.get(assignment_id)
        assignment = await lock_assignment(self.session, assignment_id)
        old_status = assignment.status
        if status == old_status:
            raise BusinessRuleViolationError(f"This fee is already {old_status.value.lower()}.")
        reason = (reason or "").strip()
        if len(reason) < 5:
            raise BusinessRuleViolationError("Say why the fee's status is being changed (it is kept on record).")
        if status in (AssignmentStatus.WAIVED, AssignmentStatus.CANCELLED) and not approved:
            # Waiving or cancelling a fee needs a second person (owner decision 10). Returns the request instead.
            from app.approvals.service import gate

            owed = await outstanding_on(self.session, assignment)
            payer = await self.resolve_payer_name(assignment.payer_type, assignment.payer_id)
            structure = await self.structure_repo.get_by_id(assignment.fee_structure_id)
            req = await gate(self.session, kind="FEE_STATUS", actor_id=actor_id, target_id=assignment.id, amount=owed, reason=reason,
                             payload={"assignment_id": assignment.id, "status": status.value, "reason": reason},
                             summary=f"{'Waive' if status == AssignmentStatus.WAIVED else 'Cancel'} {structure.name if structure else 'fee'}"
                                     f" for {payer} (still owed Rs. {owed:,.0f})")
            if req is not None:
                return req
        assignment.status = status
        await sync_challans(self.session, assignment)
        await self.audit.log(
            actor_user_id=actor_id,
            action="fee_assignment.status_changed",
            entity_type="fee_assignment",
            entity_id=assignment.id,
            before={"status": old_status.value},
            after={"status": status.value},
            metadata={"reason": reason},
        )
        return assignment

    async def list_for_payer(self, payer_type: PayerType, payer_id: uuid.UUID) -> list[FeeAssignment]:
        return await self.repo.list_for_payer(payer_type, payer_id)

    async def list_all(self) -> list[FeeAssignment]:
        return await self.repo.list_all()


class ChallanService:
    """Challans (one bank slip per installment) and the payments made through them. Every path that marks a challan
    paid -- a bank confirmation, the simulated gateway, an accounts officer -- goes through _mark_paid, so the same
    checks apply to all of them: the fee must be active, the money must still be owed, and the payment settles the
    challan's own installment first."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ChallanRepository(session)
        self.assignment_repo = FeeAssignmentRepository(session)
        self.audit = AuditService(session)

    async def get(self, challan_id: uuid.UUID) -> PaymentChallan:
        challan = await self.repo.get_by_id(challan_id)
        if challan is None:
            raise ResourceNotFoundError("Challan not found.")
        return challan

    async def _locked(self, challan_id: uuid.UUID) -> tuple[PaymentChallan, FeeAssignment]:
        """Lock the fee, then re-read the challan, so two people can never pay (or reverse) it at the same moment."""
        challan = await self.get(challan_id)
        assignment = await lock_assignment(self.session, challan.fee_assignment_id)
        challan = (await self.session.execute(select(PaymentChallan).where(PaymentChallan.id == challan_id)
                                              .execution_options(populate_existing=True))).scalar_one()
        return challan, assignment

    async def list_for_assignment(self, assignment_id: uuid.UUID) -> list[PaymentChallan]:
        return await self.repo.list_for_assignment(assignment_id)

    async def simulate_payment(self, challan_id: uuid.UUID, *, actor_id: uuid.UUID) -> PaymentChallan:
        """Student-facing (or Admin, for demo purposes) simulated payment. Refused on the live system by the router."""
        challan, assignment = await self._locked(challan_id)
        if challan.status != ChallanStatus.PENDING:
            raise BusinessRuleViolationError(f"This challan is already {challan.status.value.lower()}.")
        result = await get_payment_gateway().simulate_payment(challan.consumer_number)
        if not result.get("success"):
            raise BusinessRuleViolationError("Payment simulation failed.")
        return await self._mark_paid(challan, assignment, gateway_reference=result["gateway_reference"], paid_at=result["paid_at"], actor_id=actor_id)

    async def confirm_from_webhook(self, *, consumer_number: str, gateway_reference: str) -> PaymentChallan:
        """What a REAL 1LINK callback would invoke (the webhook router is not mounted yet)."""
        stmt = select(PaymentChallan).where(PaymentChallan.consumer_number == consumer_number)
        found = (await self.session.execute(stmt)).scalar_one_or_none()
        if found is None:
            raise ResourceNotFoundError("No challan matches this consumer number.")
        challan, assignment = await self._locked(found.id)
        if challan.status == ChallanStatus.PAID:
            if challan.gateway_reference == gateway_reference:
                return challan                                   # the bank sent the same confirmation twice: nothing more to do
            raise BusinessRuleViolationError("This challan is already paid with another reference.")
        # The money has really arrived, so it is always recorded: even if the challan was cancelled meanwhile (paid at the
        # counter) or is more than is now owed. Whatever is not owed is held as advance credit (owner decision 5).
        return await self._mark_paid(challan, assignment, gateway_reference=gateway_reference, paid_at=datetime.now(timezone.utc), actor_id=None,
                                     accept_excess=True)

    async def mark_paid_by_staff(
        self, challan_id: uuid.UUID, *, actor_id: uuid.UUID, note: str | None = None,
        method: str = "BANK_DEPOSIT", reference: str | None = None, paid_on: date | None = None,
    ) -> PaymentChallan:
        """An accounts officer confirms a challan was paid (the main way payments are recorded while the 1LINK gateway
        is not live)."""
        challan, assignment = await self._locked(challan_id)
        if challan.status != ChallanStatus.PENDING:
            raise BusinessRuleViolationError(f"This challan is already {challan.status.value.lower()}.")
        day = validate_paid_on(paid_on)
        from app.accounts.period import ensure_open
        from app.fees.references import refuse_reused_reference

        await ensure_open(self.session, day, "This payment")
        await refuse_reused_reference(self.session, reference, method)
        challan = await self._mark_paid(
            challan, assignment, gateway_reference=reference or f"CASH-{uuid.uuid4().hex[:8].upper()}",
            paid_at=datetime.now(timezone.utc), actor_id=actor_id, method=method, paid_on=day,
        )
        await self.audit.log(
            actor_user_id=actor_id,
            action="fee.payment_marked_paid_by_staff",
            entity_type="payment_challan",
            entity_id=challan.id,
            metadata={"note": note, "method": method, "reference": reference, "paid_on": str(day)},
        )
        return challan

    async def payment_is_reversed(self, entry_id: uuid.UUID) -> bool:
        e = await self.session.get(FeeLedgerEntry, entry_id)
        return e is not None and await credit_is_reversed(self.session, e)

    async def _reopen_challan_of(self, credit: FeeLedgerEntry) -> PaymentChallan | None:
        """The challan this payment paid (if any) is payable again once the payment is taken back."""
        challans = (await self.session.execute(select(PaymentChallan).where(
            PaymentChallan.fee_assignment_id == credit.fee_assignment_id, PaymentChallan.status == ChallanStatus.PAID))).scalars().all()
        for ch in challans:
            if ch.paid_entry_id == credit.id or (ch.paid_entry_id is None and _mentions_challan(credit, ch.consumer_number)):
                ch.status, ch.gateway_reference, ch.paid_at, ch.paid_entry_id = ChallanStatus.PENDING, None, None, None
                return ch
        return None

    async def reverse_payment(self, entry_id: uuid.UUID, *, reason: str, actor_id: uuid.UUID) -> FeeLedgerEntry:
        """Take back a recorded payment (entered by mistake, or a cheque that bounced). The ledger is append-only, so the payment
        stays and a matching reversal is added; the receipt then prints as VOID and the amount is owed again. A payment can be
        reversed once only, whichever screen it is reversed from."""
        from app.accounts.period import ensure_open
        from app.core.clock import local_date

        e = await self.session.get(FeeLedgerEntry, entry_id)
        if e is None or e.entry_type != LedgerEntryType.CREDIT or not e.receipt_number:
            raise ResourceNotFoundError("Payment not found.")
        if len((reason or "").strip()) < 5:
            raise ValidationAppError("Say why the payment is being reversed.")
        assignment = await lock_assignment(self.session, e.fee_assignment_id)
        if await credit_is_reversed(self.session, e):
            raise BusinessRuleViolationError("This payment was already reversed.")
        await ensure_open(self.session, e.paid_on or local_date(e.created_at), "This payment")
        today = institute_today()
        reversal = FeeLedgerEntry(fee_assignment_id=e.fee_assignment_id, entry_type=LedgerEntryType.DEBIT, amount=float(e.amount), due_date=today,
                                  related_entry_id=e.id, created_by=actor_id, created_at=datetime.now(timezone.utc),
                                  description=f"{FeeStatusCalculator.REVERSAL_PREFIX} \u2014 receipt {e.receipt_number} reversed by staff ({reason.strip()})")
        self.session.add(reversal)
        reopened = await self._reopen_challan_of(e)
        await sync_challans(self.session, assignment)
        await self.audit.log(actor_user_id=actor_id, action="fee.payment_reversed", entity_type="fee_ledger_entry", entity_id=e.id,
                             before={"receipt": e.receipt_number, "amount": float(e.amount)},
                             after={"reversed": True, "challan_reopened": reopened.consumer_number if reopened else None}, metadata={"reason": reason.strip()})
        return reversal

    async def mark_unpaid(
        self, challan_id: uuid.UUID, *, actor_id: uuid.UUID, reason: str | None = None
    ) -> PaymentChallan:
        """Reverse the payment that paid this challan (recorded in error, or the deposit bounced). Same effect as reversing
        that payment's receipt, and never both: if the receipt was already reversed, the challan is simply made payable again."""
        challan, assignment = await self._locked(challan_id)
        if challan.status != ChallanStatus.PAID:
            raise BusinessRuleViolationError(f"Only a PAID challan can be marked unpaid (currently {challan.status.value.lower()}).")
        credit = await _challan_credit(self.session, challan)
        old_reference = challan.gateway_reference
        reversed_now = False
        if credit is not None and not await credit_is_reversed(self.session, credit):
            from app.accounts.period import ensure_open
            from app.core.clock import local_date

            await ensure_open(self.session, credit.paid_on or local_date(credit.created_at), "This payment")
            original_debit = await self.session.get(FeeLedgerEntry, challan.fee_ledger_entry_id)
            self.session.add(FeeLedgerEntry(
                fee_assignment_id=challan.fee_assignment_id, entry_type=LedgerEntryType.DEBIT, amount=float(credit.amount),
                installment_number=original_debit.installment_number if original_debit else None,
                due_date=original_debit.due_date if original_debit else institute_today(), related_entry_id=credit.id,
                description=(f"{FeeStatusCalculator.REVERSAL_PREFIX} \u2014 challan {challan.consumer_number} marked unpaid by staff"
                             + (f" ({reason})" if reason else "")),
                created_by=actor_id, created_at=datetime.now(timezone.utc)))
            reversed_now = True
        challan.status, challan.gateway_reference, challan.paid_at, challan.paid_entry_id = ChallanStatus.PENDING, None, None, None
        await sync_challans(self.session, assignment)

        await self.audit.log(
            actor_user_id=actor_id,
            action="fee.payment_marked_unpaid_by_staff",
            entity_type="payment_challan",
            entity_id=challan.id,
            before={"status": "PAID", "gateway_reference": old_reference},
            after={"status": challan.status.value, "payment_reversed": reversed_now},
            metadata={"reason": reason} if reason else None,
        )

        payer_user_id = await _resolve_payer_user_id(self.session, assignment.payer_type, assignment.payer_id)
        if payer_user_id is not None and reversed_now:
            await create_notification(
                self.session, user_id=payer_user_id, category=NotificationCategory.FEE,
                title="A payment on your account was reversed",
                message=(
                    f"Rs. {float(credit.amount):.2f} \u2014 a previously recorded payment was reversed"
                    + (f" ({reason})" if reason else "") + ". This amount is due again."
                ),
                link="/dashboard/student" if assignment.payer_type == PayerType.STUDENT else "/dashboard/faculty",
            )

        return challan

    async def _mark_paid(
        self, challan: PaymentChallan, assignment: FeeAssignment, *, gateway_reference: str, paid_at: datetime, actor_id: uuid.UUID | None,
        method: str = "ONE_BILL", paid_on: date | None = None, accept_excess: bool = False,
    ) -> PaymentChallan:
        """The one place a challan becomes paid. The caller holds the fee's lock (lock_assignment).

        A person confirming a challan is refused when the fee is not active or the challan is more than is owed. Money
        that has already arrived from the bank or gateway (accept_excess) is always recorded; the part not owed is
        advance credit on this fee (see FeeStatusCalculator.compute "credit")."""
        outstanding = await outstanding_on(self.session, assignment)
        excess = round(max(float(challan.amount) - outstanding, 0.0), 2)
        if accept_excess:
            from app.accounts.period import ensure_open

            await ensure_open(self.session, paid_on or institute_today(), "This payment")
        elif assignment.status != AssignmentStatus.ACTIVE:
            raise BusinessRuleViolationError(f"This fee is {assignment.status.value.lower()}, so nothing can be collected on it.")
        elif float(challan.amount) > outstanding + 0.005:
            raise BusinessRuleViolationError(
                f"Only Rs. {outstanding:,.2f} is still owed on this fee, less than this challan (Rs. {float(challan.amount):,.2f}). "
                "It was probably paid another way; record the actual amount as a payment instead.")

        credit = FeeLedgerEntry(
            fee_assignment_id=challan.fee_assignment_id,
            entry_type=LedgerEntryType.CREDIT,
            amount=float(challan.amount),
            installment_number=None,
            due_date=None,
            description=(f"1Bill payment \u2014 consumer no. {challan.consumer_number} \u2014 ref {gateway_reference}" if method == "ONE_BILL"
                         else f"Challan {challan.consumer_number} paid \u2014 {method.replace('_', ' ').lower()} \u2014 ref {gateway_reference}"),
            payment_method=method,
            payment_reference=gateway_reference,
            paid_on=paid_on or institute_today(),
            receipt_number=await next_receipt_number(self.session),
            applies_to_entry_id=challan.fee_ledger_entry_id,          # it pays THIS installment first
            created_by=actor_id,
            created_at=paid_at,
        )
        self.session.add(credit)
        await self.session.flush()
        if accept_excess and excess > 0.005:
            credit.description = (credit.description + f" — Rs. {excess:,.2f} more than owed, held as advance credit")[:255]
        challan.status = ChallanStatus.PAID
        challan.gateway_reference = gateway_reference
        challan.paid_at = paid_at
        challan.paid_entry_id = credit.id
        await sync_challans(self.session, assignment)
        challan.late_fee_posted = await post_settled_late_fees(self.session, assignment, actor_id)    # not stored: told to the person

        await self.audit.log(
            actor_user_id=actor_id,
            action="fee.payment_via_1bill",
            entity_type="payment_challan",
            entity_id=challan.id,
            after={"consumer_number": challan.consumer_number, "amount": float(challan.amount), "gateway_reference": gateway_reference,
                   "receipt_number": credit.receipt_number, "held_as_credit": excess if accept_excess and excess > 0.005 else 0},
        )

        payer_user_id = await _resolve_payer_user_id(self.session, assignment.payer_type, assignment.payer_id)
        if payer_user_id is not None:
            await create_notification(
                self.session, user_id=payer_user_id, category=NotificationCategory.FEE,
                title="Payment received",
                message=f"Rs. {float(challan.amount):.2f} \u2014 consumer no. {challan.consumer_number}",
                link="/dashboard/student" if assignment.payer_type == PayerType.STUDENT else "/dashboard/faculty",
            )

        return challan


async def move_credit(session: AsyncSession, *, from_assignment_id: uuid.UUID, to_assignment_id: uuid.UUID, amount: float | None,
                      actor_id: uuid.UUID, note: str | None = None) -> dict:
    """Use advance credit (paid beyond what one fee needs) to pay another fee of the same person (owner decision 5).

    No money moves, so it is two linked ledger rows that the cash book ignores: CREDIT_OUT on the fee that was overpaid
    and CREDIT_IN on the fee it pays. At most the credit available and at most what the other fee still owes."""
    from app.accounts.period import ensure_open

    if from_assignment_id == to_assignment_id:
        raise BusinessRuleViolationError("Choose a different fee to use the credit on.")
    first, second = sorted([from_assignment_id, to_assignment_id], key=str)          # always lock in the same order
    locked = {first: await lock_assignment(session, first), second: await lock_assignment(session, second)}
    src, dst = locked[from_assignment_id], locked[to_assignment_id]
    if (src.payer_type, src.payer_id) != (dst.payer_type, dst.payer_id):
        raise BusinessRuleViolationError("Credit can only be used on another fee of the same person.")
    if dst.status != AssignmentStatus.ACTIVE:
        raise BusinessRuleViolationError(f"That fee is {dst.status.value.lower()}; nothing can be paid on it.")
    today = institute_today()
    await ensure_open(session, today, "This credit transfer")
    repo = FeeAssignmentRepository(session)
    available = FeeStatusCalculator.compute(await repo.get_ledger(src.id), None)["credit"]
    owed = await outstanding_on(session, dst)
    if available <= 0.005:
        raise BusinessRuleViolationError("There is no credit on that fee.")
    if owed <= 0.005:
        raise BusinessRuleViolationError("Nothing is owed on the fee you chose.")
    take = round(min(available, owed) if amount is None else float(amount), 2)
    if take <= 0 or take > available + 0.005 or take > owed + 0.005:
        raise BusinessRuleViolationError(f"Use at most Rs. {min(available, owed):,.2f} (credit Rs. {available:,.2f}; still owed Rs. {owed:,.2f}).")
    now = datetime.now(timezone.utc)
    out_row = FeeLedgerEntry(fee_assignment_id=src.id, entry_type=LedgerEntryType.CREDIT_OUT, amount=take, paid_on=today, created_by=actor_id, created_at=now,
                             description=("Credit used on another fee" + (f" ({note.strip()})" if note and note.strip() else ""))[:255])
    session.add(out_row)
    await session.flush()
    in_row = FeeLedgerEntry(fee_assignment_id=dst.id, entry_type=LedgerEntryType.CREDIT_IN, amount=take, paid_on=today, created_by=actor_id, created_at=now,
                            related_entry_id=out_row.id, description="Paid from advance credit on another fee")
    session.add(in_row)
    await session.flush()
    out_row.related_entry_id = in_row.id
    await sync_challans(session, dst)
    await AuditService(session).log(actor_user_id=actor_id, action="fee.credit_moved", entity_type="fee_assignment", entity_id=dst.id,
                                    metadata={"from": str(src.id), "to": str(dst.id), "amount": take, "note": note})
    return {"moved": take, "credit_left": round(available - take, 2), "still_owed": round(owed - take, 2)}