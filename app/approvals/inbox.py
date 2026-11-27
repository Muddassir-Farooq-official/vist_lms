"""My decisions: everything waiting that THIS person may approve or reject, from every module that follows the approval
rules (app/approvals/rules.py), in one list, with Approve / Reject.

Each kind of request has an adapter:
  pending(session)                 the requests waiting for a decision
  default_ok(session, user, item)  whether this person could decide it without any rule (the module's own check, unchanged)
  decide(session, item_id, ...)    carry out the decision through the module's own service (same checks, same records)
`check()` is also used by each module's own decide endpoint, so a rule is respected on every screen.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import rules
from app.auth.permissions import Permissions
from app.core.exceptions import ResourceNotFoundError


@dataclass
class Item:
    action: str
    id: uuid.UUID
    requester_id: uuid.UUID | None
    summary: str
    at: object = None
    amount: float | None = None
    ctx: dict | None = None
    link: str | None = None


def _perms(user) -> set[str]:
    return set(getattr(user, "permissions", None) or [])


async def _teacher_ctx(session: AsyncSession, offering_id) -> dict:
    from app.academics.models import CourseOffering
    from app.employees.models import Employee

    off = await session.get(CourseOffering, offering_id)
    if off is None or off.assigned_teacher_id is None:
        return {}
    emp = await session.get(Employee, off.assigned_teacher_id)
    return {"course_teacher_user_id": emp.user_id if emp else None, "department": emp.department if emp else None}


# ====================================================================== adapters
class _Gate:
    """The Approvals page's kinds (fees, scholarships, write-offs, books, make-up classes, timetable publishing)."""
    actions = ("FEE_STATUS", "SCHOLARSHIP", "SCHOLARSHIP_EDIT", "PREVIOUS_DUES_CANCEL", "PAYMENT_MOVE", "FINE_WAIVE", "BOOKS_REOPEN",
               "CLASS_REQUEST", "TIMETABLE_PUBLISH")

    async def pending(self, session):
        from app.approvals.models import ApprovalRequest

        rows = (await session.execute(select(ApprovalRequest).where(ApprovalRequest.status == "REQUESTED", ApprovalRequest.kind.in_(self.actions))
                                      .order_by(ApprovalRequest.created_at))).scalars().all()
        return [Item(r.kind, r.id, r.requested_by, r.summary, r.created_at, float(r.amount) if r.amount is not None else None,
                     {"department": await rules.department_of(session, r.requested_by)}, "/dashboard/admin/approvals") for r in rows]

    async def item(self, session, item_id):
        from app.approvals.models import ApprovalRequest

        r = await session.get(ApprovalRequest, item_id)
        if r is None or r.kind not in self.actions:
            raise ResourceNotFoundError("Request not found.")
        return Item(r.kind, r.id, r.requested_by, r.summary, r.created_at, float(r.amount) if r.amount is not None else None,
                    {"department": await rules.department_of(session, r.requested_by)})

    async def default_ok(self, session, user, item):
        return Permissions.APPROVALS_DECIDE in _perms(user)

    async def decide(self, session, item, *, approve, note, user):
        from app.approvals.service import ApprovalService

        await ApprovalService(session).decide(item.id, approve=approve, note=note, actor_id=uuid.UUID(str(user.id)), user=user)


class _AttendanceCorrection:
    actions = ("ATTENDANCE_CORRECTION",)

    async def _item(self, session, r):
        from app.academics.models import ClassSession

        cs = await session.get(ClassSession, r.class_session_id)
        ctx = await _teacher_ctx(session, cs.course_offering_id) if cs else {}
        return Item("ATTENDANCE_CORRECTION", r.id, r.requested_by, f"Attendance for {cs.session_date:%d %b %Y}: {r.reason}" if cs else r.reason,
                    r.created_at, None, ctx | {"offering_id": cs.course_offering_id if cs else None}, "/dashboard/admin/attendance")

    async def pending(self, session):
        from app.attendance.models import AttendanceCorrectionRequest

        rows = (await session.execute(select(AttendanceCorrectionRequest).where(AttendanceCorrectionRequest.status == "PENDING")
                                      .order_by(AttendanceCorrectionRequest.created_at))).scalars().all()
        return [await self._item(session, r) for r in rows]

    async def item(self, session, item_id):
        from app.attendance.models import AttendanceCorrectionRequest

        r = await session.get(AttendanceCorrectionRequest, item_id)
        if r is None:
            raise ResourceNotFoundError("Correction request not found.")
        return await self._item(session, r)

    async def default_ok(self, session, user, item):
        if not ({Permissions.ATTENDANCE_LOCK, Permissions.ATTENDANCE_CORRECTION_DECIDE} & _perms(user)):
            return False
        from app.core.scope import hod_department

        dept = await hod_department(session, user)
        return dept is None or rules._norm((item.ctx or {}).get("department")) == dept

    async def decide(self, session, item, *, approve, note, user):
        from app.attendance.service import AttendanceService

        await AttendanceService(session).decide_correction_request(item.id, approve=approve, note=note, actor_id=uuid.UUID(str(user.id)))


class _StaffLeave:
    actions = ("STAFF_LEAVE",)

    async def _item(self, session, r):
        from app.employees.models import Employee

        emp = await session.get(Employee, r.employee_id)
        who = f"{emp.first_name} {emp.last_name}".strip() if emp else "?"
        return Item("STAFF_LEAVE", r.id, emp.user_id if emp else r.requested_by, f"{who}: leave {r.first_day:%d %b} to {r.last_day:%d %b} ({r.days} day(s)). {r.reason}",
                    r.created_at, None, {"department": emp.department if emp else None}, "/dashboard/admin/staff-attendance#leave")

    async def pending(self, session):
        from app.timekeeping.models import LeaveRequest

        rows = (await session.execute(select(LeaveRequest).where(LeaveRequest.status == "REQUESTED").order_by(LeaveRequest.created_at))).scalars().all()
        return [await self._item(session, r) for r in rows]

    async def item(self, session, item_id):
        from app.timekeeping.models import LeaveRequest

        r = await session.get(LeaveRequest, item_id)
        if r is None:
            raise ResourceNotFoundError("Leave application not found.")
        return await self._item(session, r)

    async def default_ok(self, session, user, item):
        return Permissions.STAFF_ATTENDANCE_MANAGE in _perms(user)

    async def decide(self, session, item, *, approve, note, user):
        from app.timekeeping.leave import LeaveService

        await LeaveService(session).decide(item.id, approve=approve, note=note, as_kind=None, actor_id=uuid.UUID(str(user.id)))


class _Refund:
    actions = ("REFUND",)

    async def _item(self, session, r):
        return Item("REFUND", r.id, r.requested_by, f"Refund Rs. {float(r.amount):,.0f}: {r.reason}", r.requested_at, float(r.amount),
                    {"department": await rules.department_of(session, r.requested_by)}, "/dashboard/admin/refunds")

    async def pending(self, session):
        from app.fees.refund_models import FeeRefund, RefundStatus

        rows = (await session.execute(select(FeeRefund).where(FeeRefund.status == RefundStatus.REQUESTED).order_by(FeeRefund.requested_at))).scalars().all()
        return [await self._item(session, r) for r in rows]

    async def item(self, session, item_id):
        from app.fees.refund_models import FeeRefund

        r = await session.get(FeeRefund, item_id)
        if r is None:
            raise ResourceNotFoundError("Refund not found.")
        return await self._item(session, r)

    async def default_ok(self, session, user, item):
        return Permissions.FEE_REFUND_APPROVE in _perms(user)

    async def decide(self, session, item, *, approve, note, user):
        from app.fees.refund_service import RefundService

        await RefundService(session).decide(item.id, approve=approve, note=note, actor_id=uuid.UUID(str(user.id)))


class _FineRequest:
    actions = ("FINE_REQUEST",)

    async def _item(self, session, f):
        return Item("FINE_REQUEST", f.id, f.issued_by, f"Fine Rs. {float(f.amount):,.0f}: {f.reason}", f.issued_at, float(f.amount),
                    {"department": await rules.department_of(session, f.issued_by)}, "/dashboard/admin/fines")

    async def pending(self, session):
        from app.fines.models import Fine, FineStatus

        rows = (await session.execute(select(Fine).where(Fine.status == FineStatus.PENDING_APPROVAL).order_by(Fine.issued_at))).scalars().all()
        return [await self._item(session, f) for f in rows]

    async def item(self, session, item_id):
        from app.fines.models import Fine

        f = await session.get(Fine, item_id)
        if f is None:
            raise ResourceNotFoundError("Fine not found.")
        return await self._item(session, f)

    async def default_ok(self, session, user, item):
        return Permissions.FINE_ISSUE in _perms(user)

    async def decide(self, session, item, *, approve, note, user):
        from app.fines.service import FineService

        svc = FineService(session)
        if approve:
            await svc.approve(item.id, actor_id=uuid.UUID(str(user.id)), note=note)
        else:
            await svc.reject(item.id, actor_id=uuid.UUID(str(user.id)), note=note or "")


class _ResultSheet:
    actions = ("RESULT_APPROVE",)

    async def _item(self, session, s):
        from app.academics.models import Course, CourseOffering

        off = await session.get(CourseOffering, s.course_offering_id)
        course = await session.get(Course, off.course_id) if off else None
        name = f"{course.code} {course.title}" if course else "a course"
        sec = f" ({off.term_label}{', section ' + off.section_label if off and off.section_label else ''})" if off else ""
        return Item("RESULT_APPROVE", s.course_offering_id, s.submitted_by, f"Result sheet: {name}{sec}", s.submitted_at, None,
                    await _teacher_ctx(session, s.course_offering_id), "/dashboard/admin/result-sheets")

    async def pending(self, session):
        from app.academics.models import ResultSheet, ResultSheetStatus

        rows = (await session.execute(select(ResultSheet).where(ResultSheet.status == ResultSheetStatus.SUBMITTED))).scalars().all()
        return [await self._item(session, s) for s in rows]

    async def item(self, session, item_id):
        from app.academics.models import ResultSheet

        s = (await session.execute(select(ResultSheet).where(ResultSheet.course_offering_id == item_id))).scalar_one_or_none()
        if s is None:
            raise ResourceNotFoundError("Result sheet not found.")
        return await self._item(session, s)

    async def default_ok(self, session, user, item):
        return Permissions.RESULT_APPROVE in _perms(user)

    async def decide(self, session, item, *, approve, note, user):
        from app.academics.result_sheet_service import ResultSheetService

        svc = ResultSheetService(session)
        if approve:
            await svc.approve(item.id, actor_id=uuid.UUID(str(user.id)))
        else:
            await svc.return_sheet(item.id, actor_id=uuid.UUID(str(user.id)), reason=note or "")


ADAPTERS = [_Gate(), _AttendanceCorrection(), _StaffLeave(), _Refund(), _FineRequest(), _ResultSheet()]
BY_ACTION = {a: ad for ad in ADAPTERS for a in ad.actions}


# ====================================================================== used everywhere
async def can(session: AsyncSession, user, item: Item) -> bool:
    ad = BY_ACTION[item.action]
    return await rules.may_decide(session, item.action, user, requester_id=item.requester_id, amount=item.amount, ctx=item.ctx,
                                  default_ok=await ad.default_ok(session, user, item))


async def check(session: AsyncSession, action: str, user, item_id) -> Item:
    """For a module's own decide endpoint: may this person decide this request (rules included)? Raises if not."""
    ad = BY_ACTION[action]
    item = await ad.item(session, item_id)
    await rules.check_decider(session, item.action, user, requester_id=item.requester_id, amount=item.amount, ctx=item.ctx,
                              default_ok=await ad.default_ok(session, user, item))
    return item


async def mine(session: AsyncSession, user) -> list[dict]:
    """Everything waiting that this person may decide now."""
    out = []
    for ad in ADAPTERS:
        for item in await ad.pending(session):
            if item.requester_id is not None and str(item.requester_id) == str(user.id):
                continue
            if await can(session, user, item):
                res = await rules.effective(session, item.action, requester_id=item.requester_id, amount=item.amount, ctx=item.ctx)
                out.append({"action": item.action, "label": rules.BY_KEY[item.action].label if item.action in rules.BY_KEY else item.action,
                            "id": item.id, "summary": item.summary, "amount": item.amount, "at": item.at, "link": item.link,
                            "requested_by": await rules._person_name(session, item.requester_id) if item.requester_id else None,
                            "decided_by": res.text})
    out.sort(key=lambda x: str(x["at"] or ""))
    return out


async def decide(session: AsyncSession, action: str, item_id, *, approve: bool, note: str | None, user) -> dict:
    item = await check(session, action, user, item_id)
    await BY_ACTION[action].decide(session, item, approve=approve, note=note, user=user)
    return {"action": action, "id": item.id, "approved": approve}
