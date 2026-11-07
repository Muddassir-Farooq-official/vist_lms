"""Staff leave applications.

  A member of staff applies (casual, sick or unpaid) from My payslips -> Leave; every administrator is told.
  An administrator approves or refuses it (Staff attendance -> Leave requests).

Approving marks each day of the leave as excused in staff attendance ("Casual leave: <reason>" ...), so:
  * the person is not counted absent or late on those days;
  * a teacher's classes on those days show in Attendance -> Teachers as needing a cover;
  * payroll:  CASUAL  uses that month's casual-leave allowance (Settings -> Payroll), so fewer other absences are forgiven;
              SICK    is paid, up to the yearly sick-leave allowance (Settings -> Payroll);
              UNPAID  is deducted at basic / working days, as "Unpaid leave (approved)".
Leave is counted in working days of the person's own timing (their days off are not counted).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError

KINDS = {"CASUAL": "Casual leave", "SICK": "Sick leave", "UNPAID": "Unpaid leave"}


def _overlap(first: date, last: date, a: date, b: date) -> tuple[date, date] | None:
    lo, hi = max(first, a), min(last, b)
    return (lo, hi) if lo <= hi else None


def _month_bounds(month: date) -> tuple[date, date]:
    start = month.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt - timedelta(days=1)


class LeaveService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def _employee_for_user(self, user_id: uuid.UUID):
        from app.employees.models import Employee

        emp = (await self.session.execute(select(Employee).where(Employee.user_id == user_id))).scalars().first()
        if emp is None:
            raise BusinessRuleViolationError("Only members of staff can apply for leave.")
        return emp

    async def _working_days(self, emp, first: date, last: date) -> list[date]:
        from app.timekeeping.service import TimekeepingService

        t = (await TimekeepingService(self.session).timings_for([emp]))[emp.id]
        out, d = [], first
        while d <= last:
            if d.weekday() in t.days:
                out.append(d)
            d += timedelta(days=1)
        return out

    async def _approved_days(self, employee_id: uuid.UUID, kind: str, first: date, last: date, *, exclude: uuid.UUID | None = None) -> int:
        """Working days of approved leave of this kind that fall between first and last."""
        from app.employees.models import Employee
        from app.timekeeping.models import LeaveRequest

        rows = (await self.session.execute(select(LeaveRequest).where(
            LeaveRequest.employee_id == employee_id, LeaveRequest.kind == kind, LeaveRequest.status == "APPROVED",
            LeaveRequest.first_day <= last, LeaveRequest.last_day >= first))).scalars().all()
        if not rows:
            return 0
        emp = await self.session.get(Employee, employee_id)
        total = 0
        for r in rows:
            if exclude is not None and r.id == exclude:
                continue
            ov = _overlap(first, last, r.first_day, r.last_day)
            if ov:
                total += len(await self._working_days(emp, *ov))
        return total

    async def _teacher(self, emp) -> bool:
        from app.auth.permissions import Roles
        from app.employees.models import JobRole

        jr = await self.session.get(JobRole, emp.job_role_id) if emp.job_role_id else None
        return jr is not None and jr.default_role_code == Roles.TEACHER

    async def balances(self, emp, on: date) -> dict:
        """Casual leave left this month and sick leave left this year (approved applications only)."""
        m0, m1 = _month_bounds(on)
        casual_allowed = int(settings.payroll_casual_leaves_teacher if await self._teacher(emp) else settings.payroll_casual_leaves_staff)
        sick_allowed = int(settings.payroll_sick_leaves_per_year)
        casual_used = await self._approved_days(emp.id, "CASUAL", m0, m1)
        sick_used = await self._approved_days(emp.id, "SICK", date(on.year, 1, 1), date(on.year, 12, 31))
        return {"casual_allowed_month": casual_allowed, "casual_used_month": casual_used, "casual_left_month": max(0, casual_allowed - casual_used),
                "sick_allowed_year": sick_allowed, "sick_used_year": sick_used, "sick_left_year": max(0, sick_allowed - sick_used)}

    async def _check_allowance(self, emp, kind: str, first: date, last: date, *, exclude: uuid.UUID | None = None) -> None:
        if kind == "CASUAL":
            allowed = int(settings.payroll_casual_leaves_teacher if await self._teacher(emp) else settings.payroll_casual_leaves_staff)
            month = first.replace(day=1)
            while month <= last:
                m0, m1 = _month_bounds(month)
                ov = _overlap(first, last, m0, m1)
                asked = len(await self._working_days(emp, *ov)) if ov else 0
                used = await self._approved_days(emp.id, "CASUAL", m0, m1, exclude=exclude)
                if used + asked > allowed:
                    raise BusinessRuleViolationError(
                        f"Casual leave in {m0:%B %Y}: {allowed} day(s) allowed, {used} already approved, {asked} asked. "
                        "Apply for the extra days as unpaid leave (or sick leave if you were ill).")
                month = m1 + timedelta(days=1)
        elif kind == "SICK":
            for year in range(first.year, last.year + 1):
                y0, y1 = date(year, 1, 1), date(year, 12, 31)
                ov = _overlap(first, last, y0, y1)
                asked = len(await self._working_days(emp, *ov)) if ov else 0
                used = await self._approved_days(emp.id, "SICK", y0, y1, exclude=exclude)
                if used + asked > int(settings.payroll_sick_leaves_per_year):
                    raise BusinessRuleViolationError(
                        f"Sick leave in {year}: {int(settings.payroll_sick_leaves_per_year)} day(s) a year, {used} already approved, {asked} asked. "
                        "The extra days can be approved as unpaid leave.")

    # ------------------------------------------------------------------ applying
    async def apply(self, *, user_id: uuid.UUID, kind: str, first: date, last: date, reason: str) -> dict:
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification
        from app.timekeeping.models import LeaveRequest

        emp = await self._employee_for_user(user_id)
        kind = (kind or "").upper()
        if kind not in KINDS:
            raise BusinessRuleViolationError("Choose casual, sick or unpaid leave.")
        if last < first:
            raise BusinessRuleViolationError("The last day is before the first day.")
        if (last - first).days > 90:
            raise BusinessRuleViolationError("Apply for at most 90 days at a time.")
        if len((reason or "").strip()) < 5:
            raise BusinessRuleViolationError("Write the reason (a few words).")
        from app.core.clock import institute_today

        if first < institute_today() - timedelta(days=30):
            raise BusinessRuleViolationError("Leave can be applied for at most 30 days after it was taken. Ask the office to excuse older days.")
        days = await self._working_days(emp, first, last)
        if not days:
            raise BusinessRuleViolationError("Those are all your days off: no leave is needed.")
        clash = (await self.session.execute(select(LeaveRequest).where(
            LeaveRequest.employee_id == emp.id, LeaveRequest.status.in_(["REQUESTED", "APPROVED"]),
            LeaveRequest.first_day <= last, LeaveRequest.last_day >= first))).scalars().first()
        if clash is not None:
            raise BusinessRuleViolationError(f"You already have leave {clash.status.lower()} for {clash.first_day:%d %b} to {clash.last_day:%d %b}.")
        await self._check_allowance(emp, kind, first, last)
        r = LeaveRequest(employee_id=emp.id, kind=kind, first_day=first, last_day=last, days=len(days), reason=reason.strip()[:500],
                         status="REQUESTED", requested_by=user_id)
        self.session.add(r)
        await self.session.flush()
        await self.audit.log(actor_user_id=user_id, action="leave.requested", entity_type="leave_request", entity_id=r.id,
                             after={"kind": kind, "from": str(first), "to": str(last), "days": len(days)})
        from app.approvals.service import _users_with_permission
        from app.auth.permissions import Permissions

        for uid in await _users_with_permission(self.session, Permissions.STAFF_ATTENDANCE_MANAGE, exclude=user_id):
            await create_notification(self.session, user_id=uid, category=NotificationCategory.GENERAL, title="Leave application",
                                      message=f"{emp.first_name} {emp.last_name}: {KINDS[kind].lower()}, {first:%d %b} to {last:%d %b} ({len(days)} day(s)).",
                                      link="/dashboard/admin/staff-attendance#leave")
        return self.out(r, emp)

    async def cancel(self, request_id: uuid.UUID, *, user_id: uuid.UUID) -> dict:
        r = await self._get(request_id)
        emp = await self._employee_for_user(user_id)
        if r.employee_id != emp.id:
            raise ResourceNotFoundError("Leave application not found.")
        if r.status != "REQUESTED":
            raise BusinessRuleViolationError("Only an application still waiting for a decision can be withdrawn; ask the office about approved leave.")
        r.status = "CANCELLED"
        await self.audit.log(actor_user_id=user_id, action="leave.cancelled", entity_type="leave_request", entity_id=r.id)
        return self.out(r, emp)

    async def _get(self, request_id: uuid.UUID):
        from app.timekeeping.models import LeaveRequest

        r = (await self.session.execute(select(LeaveRequest).where(LeaveRequest.id == request_id).with_for_update())).scalar_one_or_none()
        if r is None:
            raise ResourceNotFoundError("Leave application not found.")
        return r

    # ------------------------------------------------------------------ deciding
    async def decide(self, request_id: uuid.UUID, *, approve: bool, note: str | None, as_kind: str | None, actor_id: uuid.UUID) -> dict:
        from app.employees.models import Employee
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification
        from app.timekeeping.service import TimekeepingService

        r = await self._get(request_id)
        if r.status != "REQUESTED":
            raise BusinessRuleViolationError(f"This application is already {r.status.lower()}.")
        emp = await self.session.get(Employee, r.employee_id)
        note = (note or "").strip()
        if not approve and len(note) < 5:
            raise BusinessRuleViolationError("Say why it is refused (the applicant sees this).")
        if approve:
            if as_kind:
                as_kind = as_kind.upper()
                if as_kind not in KINDS:
                    raise BusinessRuleViolationError("Choose casual, sick or unpaid leave.")
                if as_kind != r.kind:
                    note = (f"Approved as {KINDS[as_kind].lower()} (asked: {KINDS[r.kind].lower()}). " + note).strip()
                    r.kind = as_kind
            await self._check_allowance(emp, r.kind, r.first_day, r.last_day, exclude=r.id)
            await TimekeepingService(self.session).excuse_range(emp.id, r.first_day, r.last_day, f"{KINDS[r.kind]}: {r.reason}"[:200], actor_id=actor_id)
            await self._reset_off_days(emp, r.first_day, r.last_day)
            r.status = "APPROVED"
        else:
            r.status = "REJECTED"
        r.decided_by, r.decided_at, r.decision_note = actor_id, datetime.now(timezone.utc), note[:300] or None
        await self.audit.log(actor_user_id=actor_id, action="leave.approved" if approve else "leave.rejected", entity_type="leave_request", entity_id=r.id,
                             after={"kind": r.kind, "from": str(r.first_day), "to": str(r.last_day), "days": r.days, "note": r.decision_note})
        if emp.user_id:
            await create_notification(self.session, user_id=emp.user_id, category=NotificationCategory.GENERAL,
                                      title="Leave approved" if approve else "Leave not approved",
                                      message=f"{KINDS[r.kind]}, {r.first_day:%d %b} to {r.last_day:%d %b}" + (f": {r.decision_note}" if r.decision_note else "."),
                                      link=await self._pay_page(emp.user_id))
        await self.session.flush()
        return self.out(r, emp)

    async def _pay_page(self, user_id: uuid.UUID) -> str | None:
        """The person's own "My pay & attendance" page, where the Leave panel is."""
        from app.auth.models import Role, UserRole
        from app.auth.redirects import profile_for_roles

        roles = list((await self.session.execute(select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id))).scalars())
        prof = profile_for_roles(roles)
        return prof.rsplit("/", 1)[0] + "/payslips#leave" if prof and not prof.startswith("/dashboard/student") else None

    async def _reset_off_days(self, emp, first: date, last: date) -> None:
        """excuse_range excuses every calendar day; days off stay plain days off (they are not leave)."""
        from app.timekeeping.models import StaffAttendance

        rows = (await self.session.execute(select(StaffAttendance).where(StaffAttendance.employee_id == emp.id, StaffAttendance.work_date >= first,
                                                                        StaffAttendance.work_date <= last, StaffAttendance.is_working_day.is_(False)))).scalars()
        for row in rows:
            if row.check_in_at is None:
                row.excused, row.excuse_reason = False, None

    # ------------------------------------------------------------------ lists
    def out(self, r, emp=None) -> dict:
        return {"id": r.id, "employee_id": r.employee_id, "employee_name": f"{emp.first_name} {emp.last_name}" if emp else None,
                "employee_code": getattr(emp, "employee_code", None) if emp else None, "kind": r.kind, "kind_label": KINDS.get(r.kind, r.kind),
                "first_day": r.first_day, "last_day": r.last_day, "days": r.days, "reason": r.reason, "status": r.status,
                "decision_note": r.decision_note, "decided_at": r.decided_at, "created_at": r.created_at}

    async def mine(self, user_id: uuid.UUID) -> dict:
        from app.core.clock import institute_today
        from app.timekeeping.models import LeaveRequest

        emp = await self._employee_for_user(user_id)
        rows = (await self.session.execute(select(LeaveRequest).where(LeaveRequest.employee_id == emp.id)
                                           .order_by(LeaveRequest.first_day.desc()).limit(100))).scalars().all()
        return {"requests": [self.out(r, emp) for r in rows], "balances": await self.balances(emp, institute_today())}

    async def listing(self, *, status: str | None) -> list[dict]:
        from app.employees.models import Employee
        from app.timekeeping.models import LeaveRequest

        stmt = select(LeaveRequest, Employee).join(Employee, Employee.id == LeaveRequest.employee_id)
        if status:
            stmt = stmt.where(LeaveRequest.status == status)
        stmt = stmt.order_by(LeaveRequest.status != "REQUESTED", LeaveRequest.first_day.desc()).limit(500)
        out = []
        for r, e in (await self.session.execute(stmt)).all():
            row = self.out(r, e)
            if r.status == "REQUESTED":
                row["balances"] = await self.balances(e, r.first_day)
            out.append(row)
        return out

    async def waiting_count(self) -> int:
        from app.timekeeping.models import LeaveRequest

        return int((await self.session.execute(select(func.count()).select_from(LeaveRequest).where(LeaveRequest.status == "REQUESTED"))).scalar_one())


async def approved_days_in_month(session: AsyncSession, employee_id: uuid.UUID, kind: str, month: date) -> int:
    """For payroll: working days of approved leave of this kind in the month."""
    m0, m1 = _month_bounds(month)
    return await LeaveService(session)._approved_days(employee_id, kind, m0, m1)
