"""Second-person approval: ask, then another administrator approves (which carries the action out) or rejects.

Each kind of action registers how to carry it out (EXECUTORS). The services that own those actions call `gate()` before
doing anything: it returns None when the action may go ahead now (nobody else can approve), or the stored request.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.models import ApprovalRequest
from app.audit.service import AuditService
from app.auth.permissions import Permissions
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError

LABELS = {
    "FEE_STATUS": "Waive or cancel a fee",
    "FINE_WAIVE": "Waive a fine",
    "BOOKS_REOPEN": "Reopen closed books",
    "PREVIOUS_DUES_CANCEL": "Write off previous dues",
    "SCHOLARSHIP": "Scholarship or discount",
}
EXECUTORS: dict[str, Callable[[AsyncSession, ApprovalRequest, uuid.UUID], Awaitable[dict]]] = {}


def executor(kind: str):
    def wrap(fn):
        EXECUTORS[kind] = fn
        return fn
    return wrap


def _jsonable(v: Any) -> Any:
    if isinstance(v, (uuid.UUID, date, datetime)):
        return v.isoformat() if not isinstance(v, uuid.UUID) else str(v)
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if hasattr(v, "value") and not isinstance(v, (str, int, float)):
        return v.value
    return v


async def other_approvers(session: AsyncSession, actor_id) -> int:
    from app.auth.models import Permission, RolePermission, User, UserRole

    return (await session.execute(
        select(func.count(func.distinct(User.id))).join(UserRole, UserRole.user_id == User.id)
        .join(RolePermission, RolePermission.role_id == UserRole.role_id).join(Permission, Permission.id == RolePermission.permission_id)
        .where(Permission.code == Permissions.APPROVALS_DECIDE, User.is_active.is_(True), User.id != actor_id))).scalar_one()


async def gate(session: AsyncSession, *, kind: str, actor_id, target_id=None, payload: dict, summary: str, amount=None,
               reason: str | None = None) -> ApprovalRequest | None:
    """None = go ahead now (nobody else can approve; logged as a sole-approver decision). Otherwise the stored request."""
    if await other_approvers(session, actor_id) == 0:
        await AuditService(session).log(actor_user_id=actor_id, action="approval.sole_approver", entity_type="approval", entity_id=target_id,
                                        metadata={"kind": kind, "summary": summary[:300]})
        return None
    if target_id is not None:
        open_ = (await session.execute(select(ApprovalRequest).where(ApprovalRequest.kind == kind, ApprovalRequest.target_id == target_id,
                                                                     ApprovalRequest.status == "REQUESTED"))).scalars().first()
        if open_ is not None:
            raise BusinessRuleViolationError("This is already waiting for approval (Approvals page).")
    r = ApprovalRequest(kind=kind, target_id=target_id, summary=summary[:300], amount=amount, payload=_jsonable(payload),
                        reason=(reason or "").strip() or None, status="REQUESTED", requested_by=actor_id)
    session.add(r)
    await session.flush()
    await AuditService(session).log(actor_user_id=actor_id, action="approval.requested", entity_type="approval_request", entity_id=r.id,
                                    metadata={"kind": kind, "summary": r.summary, "amount": str(amount) if amount is not None else None})
    await _notify_approvers(session, r, actor_id)
    return r


async def _notify_approvers(session: AsyncSession, r: ApprovalRequest, actor_id) -> None:
    from app.auth.models import Permission, RolePermission, User, UserRole
    from app.notifications.models import NotificationCategory
    from app.notifications.service import create_notification

    ids = (await session.execute(
        select(User.id).distinct().join(UserRole, UserRole.user_id == User.id).join(RolePermission, RolePermission.role_id == UserRole.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(Permission.code == Permissions.APPROVALS_DECIDE, User.is_active.is_(True), User.id != actor_id))).scalars().all()
    category = getattr(NotificationCategory, "TASK", None) or list(NotificationCategory)[0]
    for uid in ids:
        await create_notification(session, user_id=uid, category=category, title=f"Approval needed: {LABELS.get(r.kind, r.kind)}",
                                  message=r.summary, link="/dashboard/admin/approvals")


def pending_response(r: ApprovalRequest) -> dict:
    return {"pending_approval": True, "approval_request_id": str(r.id), "kind": r.kind,
            "message": f"Sent for approval: {r.summary}. Another administrator approves it on the Approvals page; nothing has changed yet."}


class ApprovalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def _get(self, request_id, *, lock: bool = False) -> ApprovalRequest:
        stmt = select(ApprovalRequest).where(ApprovalRequest.id == request_id)
        if lock:
            stmt = stmt.with_for_update()
        r = (await self.session.execute(stmt)).scalar_one_or_none()
        if r is None:
            raise ResourceNotFoundError("Approval request not found.")
        return r

    async def decide(self, request_id, *, approve: bool, note: str | None, actor_id) -> ApprovalRequest:
        from app.core.controls import four_eyes

        r = await self._get(request_id, lock=True)
        if r.status != "REQUESTED":
            raise BusinessRuleViolationError(f"This request is already {r.status.lower()}.")
        sole = await four_eyes(self.session, actor_id, [r.requested_by], Permissions.APPROVALS_DECIDE,
                               "You asked for this; another administrator must approve or reject it.")
        note = (note or "").strip() or None
        if not approve:
            if not note or len(note) < 3:
                raise BusinessRuleViolationError("Say why it is rejected.")
            r.status = "REJECTED"
        else:
            run = EXECUTORS.get(r.kind)
            if run is None:
                raise BusinessRuleViolationError("This kind of request cannot be carried out by this version.")
            r.result = _jsonable(await run(self.session, r, actor_id))
            r.status = "APPROVED"
        r.decided_by, r.decided_at, r.decision_note = actor_id, datetime.now(timezone.utc), note
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action=f"approval.{r.status.lower()}", entity_type="approval_request", entity_id=r.id,
                             metadata={"kind": r.kind, "summary": r.summary, "note": note, "sole_approver": sole})
        await self._tell_requester(r)
        return r

    async def cancel(self, request_id, *, actor_id) -> ApprovalRequest:
        r = await self._get(request_id, lock=True)
        if r.status != "REQUESTED":
            raise BusinessRuleViolationError(f"This request is already {r.status.lower()}.")
        if str(r.requested_by) != str(actor_id):
            raise BusinessRuleViolationError("Only the person who asked can withdraw the request.")
        r.status, r.decided_by, r.decided_at = "CANCELLED", actor_id, datetime.now(timezone.utc)
        await self.audit.log(actor_user_id=actor_id, action="approval.cancelled", entity_type="approval_request", entity_id=r.id, metadata={"kind": r.kind})
        return r

    async def _tell_requester(self, r: ApprovalRequest) -> None:
        if r.requested_by is None:
            return
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification

        category = getattr(NotificationCategory, "TASK", None) or list(NotificationCategory)[0]
        await create_notification(self.session, user_id=r.requested_by, category=category,
                                  title=f"{LABELS.get(r.kind, r.kind)}: {'approved' if r.status == 'APPROVED' else 'rejected'}",
                                  message=r.summary + (f" — {r.decision_note}" if r.decision_note else ""))

    async def listing(self, *, status: str | None, mine_for=None) -> list[dict]:
        from app.accounts.service import AccountsService

        stmt = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc()).limit(300)
        if status:
            stmt = stmt.where(ApprovalRequest.status == status)
        if mine_for is not None:
            stmt = stmt.where(ApprovalRequest.requested_by == mine_for)
        rows = (await self.session.execute(stmt)).scalars().all()
        names = await AccountsService(self.session)._names({r.requested_by for r in rows} | {r.decided_by for r in rows})
        return [{"id": r.id, "kind": r.kind, "label": LABELS.get(r.kind, r.kind), "summary": r.summary, "amount": float(r.amount) if r.amount is not None else None,
                 "reason": r.reason, "status": r.status, "requested_by": names.get(r.requested_by), "requested_by_id": r.requested_by, "requested_at": r.created_at,
                 "decided_by": names.get(r.decided_by), "decided_at": r.decided_at, "decision_note": r.decision_note} for r in rows]

    async def pending_count(self) -> int:
        return (await self.session.execute(select(func.count()).select_from(ApprovalRequest).where(ApprovalRequest.status == "REQUESTED"))).scalar_one()


# ====================================================================== how each kind is carried out
@executor("FEE_STATUS")
async def _fee_status(session: AsyncSession, r: ApprovalRequest, actor_id) -> dict:
    from app.fees.assignment_service import FeeAssignmentService
    from app.fees.models import AssignmentStatus

    p = r.payload
    a = await FeeAssignmentService(session).change_status(uuid.UUID(p["assignment_id"]), status=AssignmentStatus(p["status"]),
                                                          actor_id=r.requested_by or actor_id, reason=p.get("reason"), approved=True)
    return {"assignment_id": a.id, "status": a.status.value}


@executor("FINE_WAIVE")
async def _fine_waive(session: AsyncSession, r: ApprovalRequest, actor_id) -> dict:
    from app.fines.service import FineService

    f = await FineService(session).waive(uuid.UUID(r.payload["fine_id"]), actor_id=r.requested_by or actor_id, note=r.payload.get("note"))
    return {"fine_id": f.id, "status": f.status.value}


@executor("BOOKS_REOPEN")
async def _books_reopen(session: AsyncSession, r: ApprovalRequest, actor_id) -> dict:
    from app.accounts.period import set_closed_through

    day = date.fromisoformat(r.payload["closed_through"]) if r.payload.get("closed_through") else None
    done = await set_closed_through(session, day, actor_id=r.requested_by or actor_id, reason=r.payload.get("reason"), approved=True)
    return {"closed_through": done}


@executor("PREVIOUS_DUES_CANCEL")
async def _dues_cancel(session: AsyncSession, r: ApprovalRequest, actor_id) -> dict:
    from app.fees.opening_balance_service import OpeningBalanceService

    out = await OpeningBalanceService(session).cancel_entry(uuid.UUID(r.payload["ledger_entry_id"]), reason=r.payload.get("reason"),
                                                            actor_id=r.requested_by or actor_id)
    return out if isinstance(out, dict) else {"done": True}


@executor("SCHOLARSHIP")
async def _scholarship(session: AsyncSession, r: ApprovalRequest, actor_id) -> dict:
    from app.fees.scholarship_service import ScholarshipService

    from app.fees.models import ScholarshipKind

    p = dict(r.payload)
    for k in ("valid_from", "valid_until"):
        p[k] = date.fromisoformat(p[k]) if p.get(k) else None
    for k in ("student_id", "fee_structure_id"):
        p[k] = uuid.UUID(p[k]) if p.get(k) else None
    p["kind"] = ScholarshipKind(p["kind"])
    s, summary = await ScholarshipService(session).grant(**p, actor_id=r.requested_by or actor_id, approved=True)
    return {"scholarship_id": s.id, **summary}
