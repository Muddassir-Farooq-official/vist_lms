from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

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
                title="A fine has been issued against you",
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

    async def mark_paid(self, fine_id: uuid.UUID, *, actor_id: uuid.UUID, note: str | None = None) -> Fine:
        fine = await self.get(fine_id)
        if fine.status != FineStatus.PENDING:
            raise BusinessRuleViolationError(f"This fine is already {fine.status.value.lower()}.")

        fine.status = FineStatus.PAID
        fine.resolved_by = actor_id
        fine.resolved_at = datetime.now(UTC)
        fine.resolution_note = note

        await self.audit.log(
            actor_user_id=actor_id,
            action="fine.marked_paid",
            entity_type="fine",
            entity_id=fine.id,
            before={"status": "PENDING"},
            after={"status": "PAID"},
        )

        payer_user_id = await _resolve_user_id(self.session, fine.payer_type, fine.payer_id)
        if payer_user_id is not None:
            await create_notification(
                self.session, user_id=payer_user_id, category=NotificationCategory.FINE,
                title="Fine marked as paid", message=f"Rs. {fine.amount:.2f} — {fine.reason}",
            )

        return fine

    async def waive(self, fine_id: uuid.UUID, *, actor_id: uuid.UUID, note: str | None = None) -> Fine:
        fine = await self.get(fine_id)
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