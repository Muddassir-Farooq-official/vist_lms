from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.auth.permissions import Roles
from app.core.exceptions import (
    AuthorizationError,
    BusinessRuleViolationError,
    ResourceNotFoundError,
)
from app.employees.models import Employee
from app.fines.models import Fine, FinePayerType, FineSource, FineStatus
from app.fines.repository import FineRepository
from app.notifications.models import NotificationCategory
from app.notifications.service import create_notification
from app.students.models import Student

# Which single fine source each requesting role is restricted to —
# "stick to your own domain," enforced here rather than trusted from
# client input, so a librarian can never request (and have approved) a
# DISCIPLINARY or OTHER fine no matter what a manipulated request body
# contains. ADMIN doesn't appear here — admin uses the direct issue()
# path, which accepts any source, not the request/approve workflow.
# The accounts names and the fee-counter names are both accepted, so a fine can be recorded the same way as a fee.
FINE_PAYMENT_METHODS = {"CASH", "BANK_TRANSFER", "CHEQUE", "ONLINE", "BANK_DEPOSIT", "ONLINE_TRANSFER"}

ROLE_REQUEST_SOURCE: dict[str, FineSource] = {
    Roles.LIBRARIAN: FineSource.LIBRARY,
    Roles.ACCOUNTS_OFFICER: FineSource.OTHER,
}


def default_source_for_roles(actor_roles: list[str]) -> FineSource:
    for role, source in ROLE_REQUEST_SOURCE.items():
        if role in actor_roles:
            return source
    raise AuthorizationError("Your role is not permitted to request a fine.")


@dataclass
class PayerInfo:
    name: str
    identifier: str | None  # roll number or employee code


async def resolve_payer(session: AsyncSession, payer_type: FinePayerType, payer_id: uuid.UUID) -> PayerInfo:
    """Deliberately a standalone lookup here rather than importing
    library's BorrowerSearchService — fines shouldn't depend on the
    library module just because they share the 'student or employee'
    shape. Same simple pattern, kept local."""
    if payer_type == FinePayerType.STUDENT:
        student = await session.get(Student, payer_id)
        if student is None:
            return PayerInfo(name="Unknown student", identifier=None)
        return PayerInfo(name=f"{student.first_name} {student.last_name}", identifier=student.roll_number)

    employee = await session.get(Employee, payer_id)
    if employee is None:
        return PayerInfo(name="Unknown employee", identifier=None)
    return PayerInfo(name=f"{employee.first_name} {employee.last_name}", identifier=employee.employee_code)


async def payer_exists(session: AsyncSession, payer_type: FinePayerType, payer_id: uuid.UUID) -> bool:
    if payer_type == FinePayerType.STUDENT:
        return (await session.get(Student, payer_id)) is not None
    return (await session.get(Employee, payer_id)) is not None


async def _resolve_user_id(session: AsyncSession, payer_type: FinePayerType, payer_id: uuid.UUID) -> uuid.UUID | None:
    """The notification table is keyed on users.id, not the
    student/employee record — this is the one-line lookup to get from
    one to the other. Returns None if the payer has no linked login
    account, in which case the caller just skips notifying (nothing to
    notify)."""
    if payer_type == FinePayerType.STUDENT:
        student = await session.get(Student, payer_id)
        return student.user_id if student else None
    employee = await session.get(Employee, payer_id)
    return employee.user_id if employee else None


class FineService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = FineRepository(session)
        self.audit = AuditService(session)

    async def get(self, fine_id: uuid.UUID) -> Fine:
        fine = await self.repo.get_by_id(fine_id)
        if fine is None:
            raise ResourceNotFoundError("Fine not found.")
        return fine

    async def issue(
        self,
        *,
        payer_type: FinePayerType,
        payer_id: uuid.UUID,
        amount: float,
        reason: str,
        source: FineSource,
        actor_id: uuid.UUID,
        related_entity_type: str | None = None,
        related_entity_id: uuid.UUID | None = None,
        notify_title: str | None = None,
    ) -> Fine:
        if not await payer_exists(self.session, payer_type, payer_id):
            raise ResourceNotFoundError(f"{payer_type.value.title()} not found.")

        now = datetime.now(UTC)
        fine = Fine(
            payer_type=payer_type,
            payer_id=payer_id,
            amount=amount,
            reason=reason.strip(),
            source=source,
            status=FineStatus.PENDING,
            issued_by=actor_id,
            issued_at=now,
            approved_by=actor_id,
            approved_at=now,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
        )
        await self.repo.create(fine)

        await self.audit.log(
            actor_user_id=actor_id,
            action="fine.issued",
            entity_type="fine",
            entity_id=fine.id,
            after={
                "payer_type": payer_type.value,
                "payer_id": str(payer_id),
                "amount": amount,
                "reason": reason,
                "source": source.value,
            },
        )

        payer_user_id = await _resolve_user_id(self.session, payer_type, payer_id)
        if payer_user_id is not None:
            await create_notification(
                self.session,
                user_id=payer_user_id,
                category=NotificationCategory.FINE,
                title=notify_title or "A fine has been issued against you",
                message=f"Rs. {amount:.2f} — {reason.strip()}",
                link="/dashboard/student/fines" if payer_type == FinePayerType.STUDENT else "/dashboard/faculty/fines",
            )

        return fine

    async def request(
        self,
        *,
        payer_type: FinePayerType,
        payer_id: uuid.UUID,
        amount: float,
        reason: str,
        source: FineSource,
        actor_id: uuid.UUID,
        actor_roles: list[str],
        related_entity_type: str | None = None,
        related_entity_id: uuid.UUID | None = None,
    ) -> Fine:
        """The LIBRARIAN/ACCOUNTS_OFFICER entry point — creates a
        PENDING_APPROVAL fine, not yet chargeable and not yet visible
        to the payer (see FineRepository.list_visible_to_payer), until
        an admin approves it via approve() below.

        `source` reflects where the request actually originated (e.g.
        the library module always passes FineSource.LIBRARY, regardless
        of whether a librarian or an admin processed the return) — it
        is NOT inferred from the actor's role, because an admin using
        the same call site as a librarian must still produce a
        correctly-sourced fine. What IS enforced by role: a non-admin
        actor's `source` must match their own domain (see
        ROLE_REQUEST_SOURCE) — a librarian passing source=OTHER, or any
        other mismatch, is rejected outright, regardless of what a
        manipulated request body claims.

        If the actor is ADMIN, the request is its own approval — same
        immediate-PENDING behavior as issue() — since there is no one
        else for an admin to request approval from."""
        if not await payer_exists(self.session, payer_type, payer_id):
            raise ResourceNotFoundError(f"{payer_type.value.title()} not found.")

        auto_approved = Roles.ADMIN in actor_roles
        if not auto_approved:
            allowed_source = default_source_for_roles(actor_roles)
            if source != allowed_source:
                raise AuthorizationError(
                    f"Your role may only request {allowed_source.value.title()} fines, not {source.value.title()}."
                )

        now = datetime.now(UTC)
        fine = Fine(
            payer_type=payer_type,
            payer_id=payer_id,
            amount=amount,
            reason=reason.strip(),
            source=source,
            status=FineStatus.PENDING if auto_approved else FineStatus.PENDING_APPROVAL,
            issued_by=actor_id,
            issued_at=now,
            approved_by=actor_id if auto_approved else None,
            approved_at=now if auto_approved else None,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
        )
        await self.repo.create(fine)

        await self.audit.log(
            actor_user_id=actor_id,
            action="fine.issued" if auto_approved else "fine.requested",
            entity_type="fine",
            entity_id=fine.id,
            after={
                "payer_type": payer_type.value, "payer_id": str(payer_id), "amount": amount,
                "reason": reason, "source": source.value, "status": fine.status.value,
            },
        )

        if auto_approved:
            # Same "the payer finds out now" behavior as issue() —
            # an admin's own request needed no one else's approval.
            payer_user_id = await _resolve_user_id(self.session, payer_type, payer_id)
            if payer_user_id is not None:
                await create_notification(
                    self.session, user_id=payer_user_id, category=NotificationCategory.FINE,
                    title="A fine has been issued against you",
                    message=f"Rs. {amount:.2f} — {reason.strip()}",
                    link="/dashboard/student/fines" if payer_type == FinePayerType.STUDENT else "/dashboard/faculty/fines",
                )
        # Deliberately no notification to anyone else here — the payer
        # must not find out about a fine that was only ever requested,
        # never approved. There's no "notify all admins" pattern
        # elsewhere in this codebase; admins see pending requests via
        # the approval queue (GET /fines?status=PENDING_APPROVAL) instead.

        return fine

    async def approve(self, fine_id: uuid.UUID, *, actor_id: uuid.UUID, note: str | None = None) -> Fine:
        """The only way a librarian/accounts-officer request becomes a
        real, chargeable, payer-visible fine. This is where the "once
        approved, show it on their account" moment actually happens —
        the payer is notified here, not at request() time."""
        fine = await self.get(fine_id)
        if fine.status != FineStatus.PENDING_APPROVAL:
            raise BusinessRuleViolationError(
                f"This fine is not awaiting approval (current status: {fine.status.value})."
            )

        fine.status = FineStatus.PENDING
        fine.approved_by = actor_id
        fine.approved_at = datetime.now(UTC)

        await self.audit.log(
            actor_user_id=actor_id,
            action="fine.approved",
            entity_type="fine",
            entity_id=fine.id,
            before={"status": "PENDING_APPROVAL"},
            after={"status": "PENDING"},
            metadata={"note": note} if note else None,
        )

        payer_user_id = await _resolve_user_id(self.session, fine.payer_type, fine.payer_id)
        if payer_user_id is not None:
            await create_notification(
                self.session, user_id=payer_user_id, category=NotificationCategory.FINE,
                title="A fine has been issued against you",
                message=f"Rs. {fine.amount:.2f} — {fine.reason}",
                link="/dashboard/student/fines" if fine.payer_type == FinePayerType.STUDENT else "/dashboard/faculty/fines",
            )
        if fine.issued_by is not None and fine.issued_by != actor_id:
            await create_notification(
                self.session, user_id=fine.issued_by, category=NotificationCategory.FINE,
                title="Your fine request was approved",
                message=f"Rs. {fine.amount:.2f} — {fine.reason}",
            )

        return fine

    async def reject(self, fine_id: uuid.UUID, *, actor_id: uuid.UUID, note: str) -> Fine:
        """Denies a librarian/accounts-officer request. The payer never
        knew this existed and is never notified — only the person who
        requested it is told, with the reason."""
        fine = await self.get(fine_id)
        if fine.status != FineStatus.PENDING_APPROVAL:
            raise BusinessRuleViolationError(
                f"This fine is not awaiting approval (current status: {fine.status.value})."
            )

        fine.status = FineStatus.REJECTED
        fine.resolved_by = actor_id
        fine.resolved_at = datetime.now(UTC)
        fine.resolution_note = note

        await self.audit.log(
            actor_user_id=actor_id,
            action="fine.rejected",
            entity_type="fine",
            entity_id=fine.id,
            before={"status": "PENDING_APPROVAL"},
            after={"status": "REJECTED"},
            metadata={"note": note},
        )

        if fine.issued_by is not None and fine.issued_by != actor_id:
            await create_notification(
                self.session, user_id=fine.issued_by, category=NotificationCategory.FINE,
                title="Your fine request was rejected",
                message=f"Rs. {fine.amount:.2f} — {fine.reason} — {note}",
            )

        return fine

    async def mark_paid(self, fine_id: uuid.UUID, *, actor_id: uuid.UUID, note: str | None = None,
                        method: str | None = None, reference: str | None = None, paid_on: date | None = None) -> Fine:
        """Collect a fine. How it was paid (method, slip/cheque number, the day) is recorded and a
        receipt number is issued, so the money shows up in Accounts like any other income."""
        from app.core.clock import institute_today
        from app.core.config import settings
        from app.core.sequences import next_sequence_value

        await self.get(fine_id)
        fine = await self._locked(fine_id)            # two clicks / two cashiers: the second one sees it is already paid
        if fine.status != FineStatus.PENDING:
            raise BusinessRuleViolationError(f"This fine is already {fine.status.value.lower()}.")
        method = (method or "CASH").upper()
        if method not in FINE_PAYMENT_METHODS:
            raise BusinessRuleViolationError("Choose how the fine was paid: cash, bank transfer, cheque or online.")
        reference = (reference or "").strip() or None
        if method != "CASH" and not reference:
            raise BusinessRuleViolationError("Enter the slip, transaction or cheque number (needed for anything except cash).")
        today = institute_today()
        paid_on = paid_on or today
        if paid_on > today:
            raise BusinessRuleViolationError("The payment date cannot be in the future.")
        if (today - paid_on).days > settings.fee_payment_max_backdate_days:
            raise BusinessRuleViolationError(f"The payment date cannot be more than {settings.fee_payment_max_backdate_days} days ago.")
        from app.accounts.period import ensure_open

        await ensure_open(self.session, paid_on, "This fine payment")
        from app.fees.references import refuse_reused_reference

        await refuse_reused_reference(self.session, reference, method)

        fine.status = FineStatus.PAID
        fine.resolved_by = actor_id
        fine.resolved_at = datetime.now(UTC)
        fine.resolution_note = note
        fine.payment_method, fine.payment_reference, fine.paid_on = method, reference, paid_on
        fine.receipt_number = f"FINE-{paid_on.year}-{await next_sequence_value(self.session, f'fine_receipt:{paid_on.year}'):06d}"

        await self.audit.log(
            actor_user_id=actor_id,
            action="fine.marked_paid",
            entity_type="fine",
            entity_id=fine.id,
            before={"status": "PENDING"},
            after={"status": "PAID", "method": method, "reference": reference, "paid_on": str(paid_on), "receipt": fine.receipt_number},
        )

        payer_user_id = await _resolve_user_id(self.session, fine.payer_type, fine.payer_id)
        if payer_user_id is not None:
            await create_notification(
                self.session, user_id=payer_user_id, category=NotificationCategory.FINE,
                title="Fine marked as paid", message=f"Rs. {fine.amount:.2f} — {fine.reason}",
            )

        return fine

    async def _locked(self, fine_id: uuid.UUID) -> Fine:
        from sqlalchemy import select

        return (await self.session.execute(select(Fine).where(Fine.id == fine_id).with_for_update().execution_options(populate_existing=True))).scalar_one()

    async def reverse_payment(self, fine_id: uuid.UUID, *, actor_id: uuid.UUID, reason: str) -> Fine:
        """Take back a fine payment recorded by mistake. The fine is owed again;
        the cancelled receipt is kept (FinePaymentReversal) for the books."""
        from app.core.clock import institute_today
        from app.fines.models import FinePaymentReversal

        reason = (reason or "").strip()
        if len(reason) < 4:
            raise BusinessRuleViolationError("Write why this payment is being reversed.")
        await self.get(fine_id)
        fine = await self._locked(fine_id)
        if fine.status != FineStatus.PAID:
            raise BusinessRuleViolationError("Only a paid fine can have its payment reversed.")
        now = datetime.now(UTC)
        self.session.add(FinePaymentReversal(
            fine_id=fine.id, receipt_number=fine.receipt_number, amount=fine.amount,
            paid_on=fine.paid_on or (fine.resolved_at.date() if fine.resolved_at else institute_today()),
            payment_method=fine.payment_method, payment_reference=fine.payment_reference,
            reversed_on=institute_today(), reversed_at=now, reversed_by=actor_id, reason=reason[:300]))
        before = {"status": "PAID", "receipt": fine.receipt_number, "paid_on": str(fine.paid_on), "method": fine.payment_method}
        fine.status = FineStatus.PENDING
        fine.resolved_by = fine.resolved_at = fine.resolution_note = None
        fine.payment_method = fine.payment_reference = fine.paid_on = fine.receipt_number = None
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="fine.payment_reversed", entity_type="fine", entity_id=fine.id,
                             before=before, after={"status": "PENDING"}, metadata={"reason": reason})
        payer_user_id = await _resolve_user_id(self.session, fine.payer_type, fine.payer_id)
        if payer_user_id is not None:
            await create_notification(self.session, user_id=payer_user_id, category=NotificationCategory.FINE,
                                      title="A fine payment was reversed", message=f"Rs. {float(fine.amount):,.2f} \u2014 {fine.reason}. It is owed again ({reason}).")
        return fine

    async def waive(self, fine_id: uuid.UUID, *, actor_id: uuid.UUID, note: str | None = None) -> Fine:
        if len((note or "").strip()) < 4:
            raise BusinessRuleViolationError("Write why this fine is being waived. It is kept in the audit log.")
        await self.get(fine_id)
        fine = await self._locked(fine_id)
        if fine.status != FineStatus.PENDING:
            raise BusinessRuleViolationError(f"This fine is already {fine.status.value.lower()}.")

        fine.status = FineStatus.WAIVED
        fine.resolved_by = actor_id
        fine.resolved_at = datetime.now(UTC)
        fine.resolution_note = note

        await self.audit.log(
            actor_user_id=actor_id,
            action="fine.waived",
            entity_type="fine",
            entity_id=fine.id,
            before={"status": "PENDING"},
            after={"status": "WAIVED"},
            metadata={"note": note} if note else None,
        )

        payer_user_id = await _resolve_user_id(self.session, fine.payer_type, fine.payer_id)
        if payer_user_id is not None:
            await create_notification(
                self.session, user_id=payer_user_id, category=NotificationCategory.FINE,
                title="Fine waived", message=f"Rs. {fine.amount:.2f} — {fine.reason}",
            )

        return fine