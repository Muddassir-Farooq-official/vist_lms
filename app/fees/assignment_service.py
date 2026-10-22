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
from app.fees.models import AssignmentStatus, FeeAssignment, FeeLedgerEntry, LedgerEntryType, PayerType
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
    def installments(entries: list[FeeLedgerEntry], late_fee_per_day: float | None, *, as_of: date | None = None) -> list[dict]:
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
        debits = sorted(
            [e for e in entries if e.entry_type == LedgerEntryType.DEBIT and not FeeStatusCalculator._is_reversal(e)],
            key=lambda e: (e.due_date or date.min, e.installment_number or 0),
        )
        reversed_total = sum(float(e.amount) for e in entries if FeeStatusCalculator._is_reversal(e))
        credits = sorted([e for e in entries if e.entry_type == LedgerEntryType.CREDIT],
                         key=lambda e: (getattr(e, "paid_on", None) or (e.created_at.date() if e.created_at else date.min), e.created_at or datetime.min.replace(tzinfo=timezone.utc)))
        # Remove reversed amounts from the newest payments first.
        # A payment counts from the day it was actually PAID (paid_on), not the
        # day staff typed it in; older rows without paid_on use created_at.
        def _paid_day(c):
            return c.paid_on if getattr(c, "paid_on", None) else (c.created_at.date() if c.created_at else today)
        pool: list[list] = [[float(c.amount), _paid_day(c), c.id] for c in credits]
        left = reversed_total
        for item in reversed(pool):
            if left <= 1e-9:
                break
            take = min(item[0], left)
            item[0] -= take
            left -= take
        pool = [p for p in pool if p[0] > 1e-9]

        concession_by_debit: dict = {}
        for e in entries:
            if e.entry_type == LedgerEntryType.CONCESSION and e.related_entry_id is not None:
                concession_by_debit[e.related_entry_id] = concession_by_debit.get(e.related_entry_id, 0.0) + float(e.amount)

        rows: list[dict] = []
        idx = 0
        for d in debits:
            charged = float(d.amount)
            concession = min(concession_by_debit.get(d.id, 0.0), charged)
            owed = charged - concession
            paid = 0.0
            settled_at = None
            paid_from: list[dict] = []          # which payments settled this installment, and how much of each
            while owed - paid > 1e-9 and idx < len(pool):
                take = min(owed - paid, pool[idx][0])
                paid += take
                if take > 1e-9:
                    paid_from.append({"ledger_entry_id": pool[idx][2], "amount": round(take, 2)})
                pool[idx][0] -= take
                if owed - paid <= 1e-9:
                    settled_at = pool[idx][1]
                if pool[idx][0] <= 1e-9:
                    idx += 1
            remaining = round(max(owed - paid, 0.0), 2)
            due = d.due_date or today
            # An instalment created after its own due date (fee activated late,
            # previous dues imported) only accrues a late fee from a grace date
            # after it was created, never retroactively from the old due date.
            late_from = due
            created_day = d.created_at.date() if getattr(d, "created_at", None) else None
            if created_day and created_day > due:
                late_from = created_day + timedelta(days=settings.fee_backdated_grace_days)
            late_fee = 0.0
            if late_fee_per_day:
                if remaining > 0.005 and late_from < today:
                    late_fee = (today - late_from).days * late_fee_per_day
                elif remaining <= 0.005 and owed > 1e-9:
                    settled_on = settled_at if settled_at else today
                    if settled_on > late_from:
                        late_fee = (settled_on - late_from).days * late_fee_per_day
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
                "late_fee_posted": round(float(d.late_fee_charged_amount or 0), 2),
                "paid_from": paid_from,
            })
        return rows

    @staticmethod
    def compute(entries: list[FeeLedgerEntry], late_fee_per_day: float | None, *, as_of: date | None = None) -> dict:
        today = as_of or institute_today()
        rows = FeeStatusCalculator.installments(entries, late_fee_per_day, as_of=today)
        total_charged = sum(r["charged"] for r in rows)
        reversed_total = sum(float(e.amount) for e in entries if FeeStatusCalculator._is_reversal(e))
        total_paid = sum(float(e.amount) for e in entries if e.entry_type == LedgerEntryType.CREDIT) - reversed_total
        overdue_principal = round(sum(r["remaining"] for r in rows if r["status"] == "OVERDUE"), 2)
        accrued_fine = round(sum(r["late_fee"] for r in rows), 2)
        return {
            "total_charged": round(total_charged, 2),
            "total_paid": round(total_paid, 2),
            "overdue_principal": overdue_principal,
            "accrued_fine": accrued_fine,
            # A late fee on an instalment that has since been paid can still be posted, but it no longer keeps the payer "owing".
            "total_due_now": round(overdue_principal + sum(r["late_fee"] for r in rows if r["remaining"] > 0.005), 2),
        }

    @staticmethod
    def compute_fine_for_debit(entries: list[FeeLedgerEntry], debit_id: uuid.UUID, late_fee_per_day: float | None, *, as_of: date | None = None) -> float:
        """The currently-calculated late fee for ONE installment (what
        FeeAssignmentService.post_late_fee posts). Same FIFO settlement as
        installments(), because whether a debit is settled, and when,
        depends on payment order across the whole assignment."""
        for r in FeeStatusCalculator.installments(entries, late_fee_per_day, as_of=as_of):
            if r["ledger_entry_id"] == debit_id:
                return r["late_fee"]
        return 0.0


async def next_receipt_number(session: AsyncSession, *, year: int | None = None) -> str:
    """RCPT-<year>-<6 digits>, one institute-wide sequence per calendar year."""
    from app.core.sequences import next_sequence_value

    year = year or institute_today().year
    return f"RCPT-{year}-{await next_sequence_value(session, f'fee_receipt:{year}'):06d}"


def validate_paid_on(paid_on: date | None) -> date:
    today = institute_today()
    day = paid_on or today
    if day > today:
        raise BusinessRuleViolationError("The payment date cannot be in the future.")
    if (today - day).days > settings.fee_payment_max_backdate_days:
        raise BusinessRuleViolationError(f"The payment date is more than {settings.fee_payment_max_backdate_days} days ago. Check the date, or ask the administrator.")
    return day


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
        return FeeStatusCalculator.compute(entries, late_fee)

    async def record_payment(
        self, assignment_id: uuid.UUID, data: RecordPaymentRequest, *, actor_id: uuid.UUID
    ) -> FeeLedgerEntry:
        assignment = await self.get(assignment_id)
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
            await self.repo.get_ledger(assignment.id), float(structure.late_fee_per_day) if structure and structure.late_fee_per_day else None)
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
            entries, debit.id, float(structure.late_fee_per_day)
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

    async def change_status(
        self, assignment_id: uuid.UUID, *, status: AssignmentStatus, actor_id: uuid.UUID
    ) -> FeeAssignment:
        assignment = await self.get(assignment_id)
        old_status = assignment.status
        assignment.status = status
        await self.audit.log(
            actor_user_id=actor_id,
            action="fee_assignment.status_changed",
            entity_type="fee_assignment",
            entity_id=assignment.id,
            before={"status": old_status.value},
            after={"status": status.value},
        )
        return assignment

    async def list_for_payer(self, payer_type: PayerType, payer_id: uuid.UUID) -> list[FeeAssignment]:
        return await self.repo.list_for_payer(payer_type, payer_id)

    async def list_all(self) -> list[FeeAssignment]:
        return await self.repo.list_all()


class ChallanService:
    """Handles the student-facing 'pay' action and the (future) real
    webhook callback — both converge on the same _mark_paid method, so
    whether a payment is confirmed by a simulated click today or a real
    1LINK callback tomorrow, the ledger update logic is identical."""

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

    async def list_for_assignment(self, assignment_id: uuid.UUID) -> list[PaymentChallan]:
        return await self.repo.list_for_assignment(assignment_id)

    async def simulate_payment(self, challan_id: uuid.UUID, *, actor_id: uuid.UUID) -> PaymentChallan:
        """Student-facing (or Admin, for demo purposes) simulated payment.
        Calls the gateway's simulate_payment — with a real provider this
        method wouldn't be meaningfully callable at all, since real
        payments arrive via the webhook below, not by us initiating them."""
        challan = await self.get(challan_id)
        if challan.status != ChallanStatus.PENDING:
            raise BusinessRuleViolationError(f"This challan is already {challan.status.value.lower()}.")

        result = await get_payment_gateway().simulate_payment(challan.consumer_number)
        if not result.get("success"):
            raise BusinessRuleViolationError("Payment simulation failed.")

        return await self._mark_paid(
            challan, gateway_reference=result["gateway_reference"], paid_at=result["paid_at"], actor_id=actor_id
        )

    async def confirm_from_webhook(self, *, consumer_number: str, gateway_reference: str) -> PaymentChallan:
        """This is what a REAL 1LINK callback would invoke — matching a
        consumer_number to its challan and confirming payment, with no
        internal actor (the bank/aggregator is the source of truth here,
        not a logged-in user), which is why actor_id is None on the
        resulting audit entries."""
        stmt = select(PaymentChallan).where(PaymentChallan.consumer_number == consumer_number)
        challan = (await self.session.execute(stmt)).scalar_one_or_none()
        if challan is None:
            raise ResourceNotFoundError("No challan matches this consumer number.")
        if challan.status != ChallanStatus.PENDING:
            raise BusinessRuleViolationError(f"This challan is already {challan.status.value.lower()}.")

        return await self._mark_paid(
            challan, gateway_reference=gateway_reference, paid_at=datetime.now(timezone.utc), actor_id=None
        )

    async def mark_paid_by_staff(
        self, challan_id: uuid.UUID, *, actor_id: uuid.UUID, note: str | None = None,
        method: str = "BANK_DEPOSIT", reference: str | None = None, paid_on: date | None = None,
    ) -> PaymentChallan:
        """Accounts-officer/admin manual override — the current PRIMARY
        way a payment gets recorded while the 1LINK gateway is still
        simulated and its webhook is disabled (see app/fees/webhook_router.py).
        Same ledger effect as a real gateway confirmation
        (_mark_paid writes the CREDIT), just with a staff actor and a
        reference that clearly marks it as a manual entry rather than a
        real bank confirmation."""
        challan = await self.get(challan_id)
        if challan.status != ChallanStatus.PENDING:
            raise BusinessRuleViolationError(f"This challan is already {challan.status.value.lower()}.")

        day = validate_paid_on(paid_on)
        from app.accounts.period import ensure_open
        from app.fees.references import refuse_reused_reference

        await ensure_open(self.session, day, "This payment")
        await refuse_reused_reference(self.session, reference, method)
        # The challan must still be owed: if the instalment was settled another
        # way (a counter payment), marking the challan paid would double-collect.
        assignment = await self.assignment_repo.get_by_id(challan.fee_assignment_id)
        structure = await FeeStructureRepository(self.session).get_by_id(assignment.fee_structure_id)
        rows = FeeStatusCalculator.installments(
            await self.assignment_repo.get_ledger(assignment.id), float(structure.late_fee_per_day) if structure.late_fee_per_day else None)
        outstanding = round(sum(r["remaining"] for r in rows), 2)
        if float(challan.amount) > outstanding + 0.005:
            raise BusinessRuleViolationError(
                f"Only Rs. {outstanding:,.2f} is still owed on this fee, less than this challan (Rs. {float(challan.amount):,.2f}). "
                "It was probably paid another way; record the actual amount as a payment instead.")
        challan = await self._mark_paid(
            challan, gateway_reference=reference or f"CASH-{uuid.uuid4().hex[:8].upper()}",
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
        return (await self.session.execute(select(func.count()).select_from(FeeLedgerEntry).where(
            FeeLedgerEntry.related_entry_id == entry_id, FeeLedgerEntry.entry_type == LedgerEntryType.DEBIT,
            FeeLedgerEntry.description.like(FeeStatusCalculator.REVERSAL_PREFIX + "%")))).scalar_one() > 0

    async def reverse_payment(self, entry_id: uuid.UUID, *, reason: str, actor_id: uuid.UUID) -> FeeLedgerEntry:
        """Take back a recorded payment (entered by mistake, or a cheque that bounced). The ledger is append-only, so the payment
        stays and a matching charge is added; the receipt then prints as VOID and the amount is owed again."""
        from app.accounts.period import ensure_open
        from app.core.clock import local_date

        e = await self.session.get(FeeLedgerEntry, entry_id)
        if e is None or e.entry_type != LedgerEntryType.CREDIT or not e.receipt_number:
            raise ResourceNotFoundError("Payment not found.")
        if len((reason or "").strip()) < 5:
            raise ValidationAppError("Say why the payment is being reversed.")
        if await self.payment_is_reversed(e.id):
            raise BusinessRuleViolationError("This payment was already reversed.")
        await ensure_open(self.session, e.paid_on or local_date(e.created_at), "This payment")
        today = institute_today()
        reversal = FeeLedgerEntry(fee_assignment_id=e.fee_assignment_id, entry_type=LedgerEntryType.DEBIT, amount=float(e.amount), due_date=today,
                                  related_entry_id=e.id, created_by=actor_id, created_at=datetime.now(timezone.utc),
                                  description=f"{FeeStatusCalculator.REVERSAL_PREFIX} \u2014 receipt {e.receipt_number} reversed by staff ({reason.strip()})")
        self.session.add(reversal)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="fee.payment_reversed", entity_type="fee_ledger_entry", entity_id=e.id,
                             before={"receipt": e.receipt_number, "amount": float(e.amount)}, after={"reversed": True}, metadata={"reason": reason.strip()})
        return reversal

    async def mark_unpaid(
        self, challan_id: uuid.UUID, *, actor_id: uuid.UUID, reason: str | None = None
    ) -> PaymentChallan:
        """Reverses a staff-marked (or simulated) payment — e.g. it was
        recorded in error, or a cash payment was later found to have
        bounced. The ledger is append-only (see app/fees/models.py), so
        this never edits or deletes the original CREDIT — it adds a new
        DEBIT for the same amount, which puts the balance right back
        where it was before the payment. The challan itself goes back
        to PENDING so it can be paid (or marked paid) again.

        The reversal reuses the ORIGINAL installment's due_date and
        installment_number (via the challan's 1:1 link to it,
        fee_ledger_entry_id) rather than dating it "today" — a found-
        during-review issue: dating it today could make the FIFO
        settlement (FeeStatusCalculator) treat a DIFFERENT installment
        as the newly-unpaid one after multiple payments. The total owed
        still came out right either way, but reusing the original due
        date keeps it recognizably THIS installment that's unpaid
        again, not some other one."""
        challan = await self.get(challan_id)
        if challan.status != ChallanStatus.PAID:
            raise BusinessRuleViolationError(f"Only a PAID challan can be marked unpaid (currently {challan.status.value.lower()}).")

        original_debit = await self.session.get(FeeLedgerEntry, challan.fee_ledger_entry_id)

        now = datetime.now(timezone.utc)
        old_reference = challan.gateway_reference

        reversal = FeeLedgerEntry(
            fee_assignment_id=challan.fee_assignment_id,
            entry_type=LedgerEntryType.DEBIT,
            amount=float(challan.amount),
            installment_number=original_debit.installment_number if original_debit else None,
            due_date=original_debit.due_date if original_debit else now.date(),
            description=(
                f"{FeeStatusCalculator.REVERSAL_PREFIX} \u2014 challan {challan.consumer_number} marked unpaid by staff"
                + (f" ({reason})" if reason else "")
            ),
            created_by=actor_id,
            created_at=now,
        )
        self.session.add(reversal)

        challan.status = ChallanStatus.PENDING
        challan.gateway_reference = None
        challan.paid_at = None

        await self.audit.log(
            actor_user_id=actor_id,
            action="fee.payment_marked_unpaid_by_staff",
            entity_type="payment_challan",
            entity_id=challan.id,
            before={"status": "PAID", "gateway_reference": old_reference},
            after={"status": "PENDING"},
            metadata={"reason": reason} if reason else None,
        )

        assignment = await self.assignment_repo.get_by_id(challan.fee_assignment_id)
        payer_user_id = (
            await _resolve_payer_user_id(self.session, assignment.payer_type, assignment.payer_id)
            if assignment is not None else None
        )
        if payer_user_id is not None:
            await create_notification(
                self.session, user_id=payer_user_id, category=NotificationCategory.FEE,
                title="A payment on your account was reversed",
                message=(
                    f"Rs. {float(challan.amount):.2f} — a previously recorded payment was reversed"
                    + (f" ({reason})" if reason else "") + ". This amount is due again."
                ),
                link="/dashboard/student" if assignment.payer_type == PayerType.STUDENT else "/dashboard/faculty",
            )

        return challan

    async def _mark_paid(
        self, challan: PaymentChallan, *, gateway_reference: str, paid_at: datetime, actor_id: uuid.UUID | None,
        method: str = "ONE_BILL", paid_on: date | None = None,
    ) -> PaymentChallan:
        challan.status = ChallanStatus.PAID
        challan.gateway_reference = gateway_reference
        challan.paid_at = paid_at

        # The actual money-received event — a CREDIT ledger entry, same
        # ledger every other payment path writes to, so status
        # calculations (FeeStatusCalculator) don't need to know or care
        # whether a payment came from a bank callback or a manual entry.
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
            created_by=actor_id,
            created_at=paid_at,
        )
        self.session.add(credit)

        await self.audit.log(
            actor_user_id=actor_id,
            action="fee.payment_via_1bill",
            entity_type="payment_challan",
            entity_id=challan.id,
            after={"consumer_number": challan.consumer_number, "amount": float(challan.amount), "gateway_reference": gateway_reference},
        )

        assignment = await self.assignment_repo.get_by_id(challan.fee_assignment_id)
        if assignment is not None:
            payer_user_id = await _resolve_payer_user_id(self.session, assignment.payer_type, assignment.payer_id)
            if payer_user_id is not None:
                await create_notification(
                    self.session, user_id=payer_user_id, category=NotificationCategory.FEE,
                    title="Payment received",
                    message=f"Rs. {float(challan.amount):.2f} — consumer no. {challan.consumer_number}",
                    link="/dashboard/student" if assignment.payer_type == PayerType.STUDENT else "/dashboard/faculty",
                )

        return challan