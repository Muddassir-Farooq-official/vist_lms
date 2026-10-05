from __future__ import annotations

from app.core.clock import institute_today

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
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

    @staticmethod
    def compute(entries: list[FeeLedgerEntry], late_fee_per_day: float | None, *, as_of: date | None = None) -> dict:
        today = as_of or institute_today()
        debits = sorted(
            [e for e in entries if e.entry_type == LedgerEntryType.DEBIT],
            key=lambda e: e.due_date or date.min,
        )
        credits = sorted(
            [e for e in entries if e.entry_type == LedgerEntryType.CREDIT],
            key=lambda e: e.created_at,
        )

        total_charged = sum(float(d.amount) for d in debits)
        total_paid = sum(float(c.amount) for c in credits)

        payment_idx = 0
        remaining_in_current_payment = float(credits[0].amount) if credits else 0.0

        overdue_principal = 0.0
        accrued_fine = 0.0

        for debit in debits:
            remaining_owed = float(debit.amount)
            settled_date: datetime | None = None

            while remaining_owed > 1e-9 and payment_idx < len(credits):
                if remaining_in_current_payment <= 1e-9:
                    payment_idx += 1
                    if payment_idx >= len(credits):
                        break
                    remaining_in_current_payment = float(credits[payment_idx].amount)

                take = min(remaining_owed, remaining_in_current_payment)
                remaining_owed -= take
                remaining_in_current_payment -= take
                if remaining_owed <= 1e-9:
                    settled_date = credits[payment_idx].created_at

            due_date = debit.due_date or today
            if remaining_owed > 1e-9:
                if due_date < today:
                    overdue_principal += remaining_owed
                    if late_fee_per_day:
                        days_late = (today - due_date).days
                        accrued_fine += days_late * late_fee_per_day
            else:
                settled_on = settled_date.date() if settled_date else today
                if settled_on > due_date and late_fee_per_day:
                    days_late = (settled_on - due_date).days
                    accrued_fine += days_late * late_fee_per_day

        overdue_principal = round(overdue_principal, 2)
        accrued_fine = round(accrued_fine, 2)

        return {
            "total_charged": round(total_charged, 2),
            "total_paid": round(total_paid, 2),
            "overdue_principal": overdue_principal,
            "accrued_fine": accrued_fine,
            "total_due_now": round(overdue_principal + accrued_fine, 2),
        }

    @staticmethod
    def compute_fine_for_debit(entries: list[FeeLedgerEntry], debit_id: uuid.UUID, late_fee_per_day: float | None, *, as_of: date | None = None) -> float:
        """The currently-calculated fine for ONE specific installment
        (debit), not the whole assignment's total — this is what
        FeeAssignmentService.post_late_fee needs to know how much of
        THIS installment's fine to post. Re-runs the exact same FIFO
        settlement as compute() above (has to — whether a debit is
        settled, and when, depends on payment order across the WHOLE
        assignment, not just this one debit) but returns this debit's
        own accrued_fine instead of the assignment-wide sum."""
        today = as_of or institute_today()
        debits = sorted(
            [e for e in entries if e.entry_type == LedgerEntryType.DEBIT],
            key=lambda e: e.due_date or date.min,
        )
        credits = sorted(
            [e for e in entries if e.entry_type == LedgerEntryType.CREDIT],
            key=lambda e: e.created_at,
        )

        payment_idx = 0
        remaining_in_current_payment = float(credits[0].amount) if credits else 0.0

        for debit in debits:
            remaining_owed = float(debit.amount)
            settled_date: datetime | None = None

            while remaining_owed > 1e-9 and payment_idx < len(credits):
                if remaining_in_current_payment <= 1e-9:
                    payment_idx += 1
                    if payment_idx >= len(credits):
                        break
                    remaining_in_current_payment = float(credits[payment_idx].amount)

                take = min(remaining_owed, remaining_in_current_payment)
                remaining_owed -= take
                remaining_in_current_payment -= take
                if remaining_owed <= 1e-9:
                    settled_date = credits[payment_idx].created_at

            due_date = debit.due_date or today
            this_debit_fine = 0.0
            if remaining_owed > 1e-9:
                if due_date < today and late_fee_per_day:
                    days_late = (today - due_date).days
                    this_debit_fine = days_late * late_fee_per_day
            else:
                settled_on = settled_date.date() if settled_date else today
                if settled_on > due_date and late_fee_per_day:
                    days_late = (settled_on - due_date).days
                    this_debit_fine = days_late * late_fee_per_day

            if debit.id == debit_id:
                return round(this_debit_fine, 2)

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
        return FeeStatusCalculator.compute(entries, late_fee)

    async def record_payment(
        self, assignment_id: uuid.UUID, data: RecordPaymentRequest, *, actor_id: uuid.UUID
    ) -> FeeLedgerEntry:
        assignment = await self.get(assignment_id)
        if assignment.status != AssignmentStatus.ACTIVE:
            raise BusinessRuleViolationError(
                f"Cannot record a payment against a {assignment.status.value.lower()} assignment."
            )

        entry = FeeLedgerEntry(
            fee_assignment_id=assignment.id,
            entry_type=LedgerEntryType.CREDIT,
            amount=data.amount,
            installment_number=None,
            due_date=None,
            description=data.description or "Payment received",
            created_by=actor_id,
            created_at=datetime.now(timezone.utc),
        )
        await self.repo.add_ledger_entry(entry)

        await self.audit.log(
            actor_user_id=actor_id,
            action="fee.payment_recorded",
            entity_type="fee_assignment",
            entity_id=assignment.id,
            after={"amount": data.amount},
        )

        payer_user_id = await _resolve_payer_user_id(self.session, assignment.payer_type, assignment.payer_id)
        if payer_user_id is not None:
            await create_notification(
                self.session, user_id=payer_user_id, category=NotificationCategory.FEE,
                title="A payment has been recorded on your account",
                message=f"Rs. {data.amount:.2f} — {entry.description}",
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
        self, challan_id: uuid.UUID, *, actor_id: uuid.UUID, note: str | None = None
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

        reference = f"STAFF-{uuid.uuid4().hex[:10].upper()}"
        challan = await self._mark_paid(
            challan, gateway_reference=reference, paid_at=datetime.now(timezone.utc), actor_id=actor_id
        )
        await self.audit.log(
            actor_user_id=actor_id,
            action="fee.payment_marked_paid_by_staff",
            entity_type="payment_challan",
            entity_id=challan.id,
            metadata={"note": note} if note else None,
        )
        return challan

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
                f"Payment reversed \u2014 challan {challan.consumer_number} marked unpaid by staff"
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
        self, challan: PaymentChallan, *, gateway_reference: str, paid_at: datetime, actor_id: uuid.UUID | None
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
            description=f"1Bill payment \u2014 consumer no. {challan.consumer_number} \u2014 ref {gateway_reference}",
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