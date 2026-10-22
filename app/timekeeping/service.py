"""Staff timekeeping: timings, marking in and out, corrections, holidays, CSV import and the monthly summary payroll uses."""
from __future__ import annotations

import calendar
import csv
import io
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
from app.employees.models import Employee, EmployeeStatus, JobRole
from app.timekeeping import rules
from app.timekeeping.models import StaffAttendance, StaffTiming

ACTIVE = (EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE)
UNDO_MINUTES = 10
FALLBACK = {"in_time": time(9, 0), "out_time": time(17, 0), "grace_in_minutes": 10, "grace_out_minutes": 0, "working_days": "0,1,2,3,4,5"}


def tz() -> ZoneInfo:
    return ZoneInfo(settings.institute_timezone)


def now_local() -> datetime:
    return datetime.now(tz())


def local(dt: datetime | None) -> datetime | None:
    return dt.astimezone(tz()) if dt else None


def at_local(day: date, clock: time) -> datetime:
    return datetime(day.year, day.month, day.day, clock.hour, clock.minute, tzinfo=tz())


def month_bounds(month: date) -> tuple[date, date]:
    first = month.replace(day=1)
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


class Timing:
    """The timing that applies to one employee, and where it came from."""

    def __init__(self, row: StaffTiming | None, source: str):
        data = {k: getattr(row, k) for k in FALLBACK} if row is not None else dict(FALLBACK)
        self.in_time: time = data["in_time"]
        self.out_time: time = data["out_time"]
        self.grace_in: int = data["grace_in_minutes"]
        self.grace_out: int = data["grace_out_minutes"]
        self.days: set[int] = rules.parse_days(data["working_days"])
        self.source = source

    def out(self) -> dict:
        return {"in_time": self.in_time.strftime("%H:%M"), "out_time": self.out_time.strftime("%H:%M"), "grace_in_minutes": self.grace_in,
                "grace_out_minutes": self.grace_out, "working_days": sorted(self.days), "days_label": rules.describe_days(self.days), "source": self.source}


def _timing_row_out(t: StaffTiming) -> dict:
    return {"id": t.id, "scope": t.scope, "job_role_id": t.job_role_id, "employee_id": t.employee_id, **Timing(t, t.scope).out()}


class TimekeepingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ timings
    async def _default_row(self) -> StaffTiming | None:
        return (await self.session.execute(select(StaffTiming).where(StaffTiming.scope == "DEFAULT"))).scalars().first()

    async def timings_for(self, employees: list[Employee]) -> dict[uuid.UUID, Timing]:
        """The timing for each employee: their own, else their job role's, else the default."""
        ids = [e.id for e in employees]
        roles = {e.job_role_id for e in employees}
        own = {t.employee_id: t for t in (await self.session.execute(select(StaffTiming).where(StaffTiming.employee_id.in_(ids or [uuid.uuid4()])))).scalars()}
        by_role = {t.job_role_id: t for t in (await self.session.execute(select(StaffTiming).where(StaffTiming.job_role_id.in_(roles or {uuid.uuid4()})))).scalars()}
        default = await self._default_row()
        out = {}
        for e in employees:
            if e.id in own:
                out[e.id] = Timing(own[e.id], "EMPLOYEE")
            elif e.job_role_id in by_role:
                out[e.id] = Timing(by_role[e.job_role_id], "JOB_ROLE")
            else:
                out[e.id] = Timing(default, "DEFAULT")
        return out

    async def timings(self) -> dict:
        default = await self._default_row()
        roles = (await self.session.execute(select(JobRole).where(JobRole.is_active.is_(True)).order_by(JobRole.title))).scalars().all()
        role_rows = {t.job_role_id: t for t in (await self.session.execute(select(StaffTiming).where(StaffTiming.scope == "JOB_ROLE"))).scalars()}
        emp_rows = (await self.session.execute(select(StaffTiming, Employee).join(Employee, Employee.id == StaffTiming.employee_id)
                                               .where(StaffTiming.scope == "EMPLOYEE").order_by(Employee.first_name))).all()
        return {"default": Timing(default, "DEFAULT").out(),
                "job_roles": [{"job_role_id": r.id, "title": r.title, "is_teacher": r.default_role_code == "TEACHER",
                               "timing": _timing_row_out(role_rows[r.id]) if r.id in role_rows else None} for r in roles],
                "employees": [{"employee_id": e.id, "name": f"{e.first_name} {e.last_name}", "employee_code": e.employee_code, "timing": _timing_row_out(t)} for t, e in emp_rows]}

    @staticmethod
    def _clean(data: dict) -> dict:
        try:
            tin = data["in_time"] if isinstance(data["in_time"], time) else time.fromisoformat(str(data["in_time"])[:5])
            tout = data["out_time"] if isinstance(data["out_time"], time) else time.fromisoformat(str(data["out_time"])[:5])
        except (KeyError, ValueError):
            raise BusinessRuleViolationError("Enter the arrival and leaving times as HH:MM.") from None
        if tout <= tin:
            raise BusinessRuleViolationError("The leaving time must be after the arrival time.")
        wd = data.get("working_days")
        days = rules.parse_days(wd) if isinstance(wd, str) else {int(x) for x in (wd or []) if 0 <= int(x) <= 6}
        if not days:
            raise BusinessRuleViolationError("Choose at least one working day.")
        gi, go = int(data.get("grace_in_minutes") or 0), int(data.get("grace_out_minutes") or 0)
        if not (0 <= gi <= 180 and 0 <= go <= 180):
            raise BusinessRuleViolationError("Relaxation must be between 0 and 180 minutes.")
        return {"in_time": tin, "out_time": tout, "grace_in_minutes": gi, "grace_out_minutes": go, "working_days": rules.format_days(days)}

    async def set_timing(self, scope: str, target_id: uuid.UUID | None, data: dict | None, *, actor_id: uuid.UUID) -> None:
        """Set (or, with data=None, remove) the default, a job role's or an employee's timing."""
        if scope == "DEFAULT":
            row = await self._default_row()
        elif scope == "JOB_ROLE":
            if await self.session.get(JobRole, target_id) is None:
                raise ResourceNotFoundError("Job role not found.")
            row = (await self.session.execute(select(StaffTiming).where(StaffTiming.job_role_id == target_id))).scalars().first()
        elif scope == "EMPLOYEE":
            if await self.session.get(Employee, target_id) is None:
                raise ResourceNotFoundError("Employee not found.")
            row = (await self.session.execute(select(StaffTiming).where(StaffTiming.employee_id == target_id))).scalars().first()
        else:
            raise BusinessRuleViolationError("Unknown timing level.")
        before = Timing(row, scope).out() if row else None
        if data is None:
            if scope == "DEFAULT":
                raise BusinessRuleViolationError("The default timing cannot be removed, only changed.")
            if row is not None:
                await self.session.delete(row)
        else:
            clean = self._clean(data)
            if row is None:
                row = StaffTiming(scope=scope, job_role_id=target_id if scope == "JOB_ROLE" else None, employee_id=target_id if scope == "EMPLOYEE" else None, **clean)
                self.session.add(row)
            else:
                for k, v in clean.items():
                    setattr(row, k, v)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="timekeeping.timing_set" if data else "timekeeping.timing_removed", entity_type="staff_timing",
                             entity_id=target_id, before=before, metadata={"scope": scope, "after": {k: str(v) for k, v in (data or {}).items()}})

    # ------------------------------------------------------------------ the day's rows
    async def _employee(self, employee_id: uuid.UUID) -> Employee:
        e = await self.session.get(Employee, employee_id)
        if e is None:
            raise ResourceNotFoundError("Employee not found.")
        return e

    async def _row(self, employee_id: uuid.UUID, day: date) -> StaffAttendance | None:
        return (await self.session.execute(select(StaffAttendance).where(StaffAttendance.employee_id == employee_id, StaffAttendance.work_date == day))).scalars().first()

    @staticmethod
    def _recompute(row: StaffAttendance) -> None:
        if row.excused:
            row.late_minutes = row.early_minutes = 0
            return
        row.late_minutes = rules.late_minutes(local(row.check_in_at), row.expected_in, row.grace_in_minutes, row.is_working_day) if row.check_in_at else 0
        row.early_minutes = rules.early_minutes(local(row.check_out_at), row.expected_out, row.grace_out_minutes, row.is_working_day) if row.check_out_at else 0

    async def _new_row(self, emp: Employee, day: date) -> StaffAttendance:
        t = (await self.timings_for([emp]))[emp.id]
        working = day.weekday() in t.days
        row = StaffAttendance(employee_id=emp.id, work_date=day, expected_in=t.in_time, expected_out=t.out_time, grace_in_minutes=t.grace_in,
                              grace_out_minutes=t.grace_out, is_working_day=working, late_minutes=0, early_minutes=0, excused=False, source="MARKED")
        self.session.add(row)
        return row

    async def mark(self, employee_id: uuid.UUID, action: str, *, actor_id: uuid.UUID) -> dict:
        """The guard / office marks someone in or out NOW."""
        emp = await self._employee(employee_id)
        if emp.status not in ACTIVE:
            raise BusinessRuleViolationError(f"{emp.first_name} {emp.last_name} is not a current member of staff.")
        moment = now_local()
        today = moment.date()
        row = await self._row(emp.id, today)
        if action == "IN":
            if row is not None and row.check_in_at is not None:
                raise BusinessRuleViolationError(f"Already marked in at {local(row.check_in_at):%H:%M}.")
            if row is None:
                row = await self._new_row(emp, today)
            row.check_in_at, row.in_marked_by = moment.astimezone(timezone.utc), actor_id
        elif action == "OUT":
            if row is None or row.check_in_at is None:
                raise BusinessRuleViolationError("Mark them in first (or ask the administration to enter the arrival time).")
            if row.check_out_at is not None:
                raise BusinessRuleViolationError(f"Already marked out at {local(row.check_out_at):%H:%M}.")
            row.check_out_at, row.out_marked_by = moment.astimezone(timezone.utc), actor_id
        else:
            raise BusinessRuleViolationError("Choose in or out.")
        self._recompute(row)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action=f"timekeeping.marked_{action.lower()}", entity_type="staff_attendance", entity_id=row.id,
                             metadata={"employee": emp.employee_code, "at": moment.strftime("%Y-%m-%d %H:%M"), "late_minutes": row.late_minutes, "early_minutes": row.early_minutes})
        return (await self.board(day=today, q=None, employee_ids=[emp.id]))[0]

    async def undo(self, employee_id: uuid.UUID, *, actor_id: uuid.UUID, admin: bool) -> dict:
        """Take back today's latest mark: your own within 10 minutes (the administration any time)."""
        today = now_local().date()
        row = await self._row(employee_id, today)
        if row is None or row.check_in_at is None:
            raise BusinessRuleViolationError("Nothing to take back today.")
        field, by = ("check_out_at", "out_marked_by") if row.check_out_at else ("check_in_at", "in_marked_by")
        if not admin and (getattr(row, by) != actor_id or now_local() - local(getattr(row, field)) > timedelta(minutes=UNDO_MINUTES)):
            raise BusinessRuleViolationError(f"You can only take back your own mark within {UNDO_MINUTES} minutes. Ask the administration to correct it.")
        setattr(row, field, None)
        setattr(row, by, None)
        self._recompute(row)
        if row.check_in_at is None and not row.excused:
            await self.session.delete(row)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="timekeeping.mark_undone", entity_type="staff_attendance", entity_id=employee_id, metadata={"field": field})
        return (await self.board(day=today, q=None, employee_ids=[employee_id]))[0]

    async def correct(self, employee_id: uuid.UUID, day: date, data: dict, *, actor_id: uuid.UUID) -> dict:
        """Administration: set the arrival / leaving time for any day, excuse the day, or clear it."""
        emp = await self._employee(employee_id)
        if day > now_local().date():
            raise BusinessRuleViolationError("You cannot record a day in the future.")
        row = await self._row(emp.id, day)
        before = self._audit_view(row)
        if data.get("clear"):
            if row is not None:
                await self.session.delete(row)
            await self.session.flush()
            await self.audit.log(actor_user_id=actor_id, action="timekeeping.day_cleared", entity_type="staff_attendance", entity_id=emp.id, before=before,
                                 metadata={"day": str(day), "reason": data.get("note")})
            return (await self.board(day=day, q=None, employee_ids=[emp.id]))[0]
        if row is None:
            row = await self._new_row(emp, day)
        tin, tout = data.get("check_in"), data.get("check_out")
        if "check_in" in data:
            row.check_in_at = at_local(day, tin).astimezone(timezone.utc) if tin else None
        if "check_out" in data:
            row.check_out_at = at_local(day, tout).astimezone(timezone.utc) if tout else None
        if row.check_out_at and not row.check_in_at:
            raise BusinessRuleViolationError("Enter the arrival time as well.")
        if row.check_in_at and row.check_out_at and row.check_out_at <= row.check_in_at:
            raise BusinessRuleViolationError("The leaving time must be after the arrival time.")
        if "excused" in data and data["excused"] is not None:
            row.excused = bool(data["excused"])
            row.excuse_reason = (data.get("excuse_reason") or "").strip() or None if row.excused else None
            if row.excused and not row.excuse_reason:
                raise BusinessRuleViolationError("Say why the day is excused (official duty, approved leave, holiday...).")
        if data.get("note") is not None:
            row.note = data["note"].strip() or None
        row.source = "CORRECTED"
        self._recompute(row)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="timekeeping.day_corrected", entity_type="staff_attendance", entity_id=row.id, before=before,
                             after=self._audit_view(row), metadata={"employee": emp.employee_code, "day": str(day)})
        return (await self.board(day=day, q=None, employee_ids=[emp.id]))[0]

    async def holiday(self, day: date, reason: str, *, actor_id: uuid.UUID) -> int:
        """Excuse a whole day for every current employee (a public holiday, a closure). Already-marked arrivals are kept."""
        reason = (reason or "").strip()
        if len(reason) < 3:
            raise BusinessRuleViolationError("Name the holiday or the reason.")
        emps = (await self.session.execute(select(Employee).where(Employee.status.in_(ACTIVE)))).scalars().all()
        count = 0
        for e in emps:
            row = await self._row(e.id, day) or await self._new_row(e, day)
            row.excused, row.excuse_reason, row.source = True, reason[:200], "CORRECTED"
            self._recompute(row)
            count += 1
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="timekeeping.holiday", entity_type="staff_attendance", entity_id=None, metadata={"day": str(day), "reason": reason, "employees": count})
        return count

    async def excuse_range(self, employee_id: uuid.UUID, first: date, last: date, reason: str, *, actor_id: uuid.UUID) -> int:
        """Approved leave: every day from `first` to `last` is excused for this employee (no absence, no late marks). Marks already made are kept."""
        emp = await self._employee(employee_id)
        reason = (reason or "").strip() or "Approved leave"
        if last < first:
            first, last = last, first
        if (last - first).days > 120:
            raise BusinessRuleViolationError("Leave of more than 120 days cannot be recorded in one go.")
        day, count = first, 0
        while day <= last:
            row = await self._row(emp.id, day) or await self._new_row(emp, day)
            row.excused, row.excuse_reason, row.source = True, reason[:200], "CORRECTED"
            self._recompute(row)
            count += 1
            day += timedelta(days=1)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="timekeeping.leave_excused", entity_type="staff_attendance", entity_id=emp.id,
                             metadata={"from": str(first), "to": str(last), "days": count, "reason": reason})
        return count

    async def my_month(self, user_id: uuid.UUID, month: date) -> dict:
        emp = (await self.session.execute(select(Employee).where(Employee.user_id == user_id))).scalars().first()
        if emp is None:
            raise BusinessRuleViolationError("You are not a member of staff, so there is no attendance for you.")
        return await self.employee_month(emp.id, month)

    async def import_csv(self, raw: bytes, *, dry_run: bool, actor_id: uuid.UUID) -> dict:
        """employee_code,date,check_in,check_out (times HH:MM, date YYYY-MM-DD) -- e.g. exported from a biometric machine."""
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise BusinessRuleViolationError("Save the file as CSV (UTF-8).") from None
        reader = csv.DictReader(io.StringIO(text))
        need = {"employee_code", "date", "check_in", "check_out"}
        if not reader.fieldnames or not need <= {h.strip().lower() for h in reader.fieldnames}:
            raise BusinessRuleViolationError("The first row must be: employee_code,date,check_in,check_out")
        codes = {e.employee_code.upper(): e for e in (await self.session.execute(select(Employee))).scalars()}
        errors, ok = [], 0
        today = now_local().date()
        for n, raw_row in enumerate(reader, start=2):
            r = {k.strip().lower(): (v or "").strip() for k, v in raw_row.items() if k}
            if not any(r.values()):
                continue
            emp = codes.get(r["employee_code"].upper())
            try:
                day = date.fromisoformat(r["date"])
                tin = time.fromisoformat(r["check_in"][:5]) if r["check_in"] else None
                tout = time.fromisoformat(r["check_out"][:5]) if r["check_out"] else None
            except ValueError:
                errors.append({"row": n, "message": "Date must be YYYY-MM-DD and times HH:MM."})
                continue
            if emp is None:
                errors.append({"row": n, "message": f"No employee with code {r['employee_code']}."})
                continue
            if day > today:
                errors.append({"row": n, "message": "The date is in the future."})
                continue
            if tin is None:
                errors.append({"row": n, "message": "The arrival time is missing."})
                continue
            if tout is not None and tout <= tin:
                errors.append({"row": n, "message": "Leaving is before arrival."})
                continue
            ok += 1
            if not dry_run:
                row = await self._row(emp.id, day) or await self._new_row(emp, day)
                row.check_in_at = at_local(day, tin).astimezone(timezone.utc)
                row.check_out_at = at_local(day, tout).astimezone(timezone.utc) if tout else None
                row.source = "IMPORTED"
                self._recompute(row)
        if not dry_run:
            await self.session.flush()
            await self.audit.log(actor_user_id=actor_id, action="timekeeping.imported", entity_type="staff_attendance", entity_id=None, metadata={"rows": ok, "errors": len(errors)})
        return {"rows_ok": ok, "errors": errors[:200], "dry_run": dry_run}

    # ------------------------------------------------------------------ reading
    @classmethod
    def _audit_view(cls, row: StaffAttendance | None) -> dict | None:
        out = cls._row_out(row)
        return {k: (v if isinstance(v, (bool, int, type(None))) else str(v)) for k, v in out.items()} if out else None

    @staticmethod
    def _row_out(row: StaffAttendance | None) -> dict | None:
        if row is None:
            return None
        li, lo = local(row.check_in_at), local(row.check_out_at)
        return {"id": row.id, "work_date": row.work_date, "check_in": li.strftime("%H:%M") if li else None, "check_out": lo.strftime("%H:%M") if lo else None,
                "expected_in": row.expected_in.strftime("%H:%M") if row.expected_in else None, "expected_out": row.expected_out.strftime("%H:%M") if row.expected_out else None,
                "is_working_day": row.is_working_day, "late_minutes": row.late_minutes, "early_minutes": row.early_minutes, "excused": row.excused,
                "excuse_reason": row.excuse_reason, "source": row.source, "note": row.note}

    async def board(self, *, day: date, q: str | None, employee_ids: list[uuid.UUID] | None = None, state: str | None = None) -> list[dict]:
        stmt = select(Employee, JobRole.title).join(JobRole, JobRole.id == Employee.job_role_id).order_by(Employee.first_name, Employee.last_name)
        if employee_ids:
            stmt = stmt.where(Employee.id.in_(employee_ids))
        else:
            stmt = stmt.where(Employee.status.in_(ACTIVE))
        if q and q.strip():
            n = f"%{q.strip().lower()}%"
            stmt = stmt.where(or_(func.lower(Employee.first_name + " " + Employee.last_name).like(n), func.lower(Employee.employee_code).like(n)))
        people = (await self.session.execute(stmt.limit(500))).all()
        emps = [e for e, _ in people]
        timing = await self.timings_for(emps)
        rows = {r.employee_id: r for r in (await self.session.execute(select(StaffAttendance).where(StaffAttendance.work_date == day,
                                                                                                     StaffAttendance.employee_id.in_([e.id for e in emps] or [uuid.uuid4()])))).scalars()}
        out = []
        for e, title in people:
            row, t = rows.get(e.id), timing[e.id]
            st = "EXCUSED" if row and row.excused else ("OUT" if row and row.check_out_at else ("IN" if row and row.check_in_at else "NOT_IN"))
            if state and st != state:
                continue
            out.append({"employee_id": e.id, "name": f"{e.first_name} {e.last_name}", "employee_code": e.employee_code, "job_title": title, "department": e.department,
                        "timing": t.out(), "working_today": day.weekday() in t.days, "state": st, "day": self._row_out(row)})
        return out

    async def summary(self, month: date, employees: list[Employee], *, through: date | None = None) -> dict[uuid.UUID, dict]:
        """Per employee, for the month: scheduled working days, present, absent (dates), late, early, missing leaving marks.
        Today is only counted once someone has been marked; days before they joined, and days before the institute
        recorded any staff attendance at all, are never counted."""
        first, last = month_bounds(month)
        today = now_local().date()
        end = min(last, through or today, today)
        timing = await self.timings_for(employees)
        # Days before the institute started recording staff attendance at all are never absences.
        started = (await self.session.execute(select(func.min(StaffAttendance.work_date)))).scalar_one()
        rows: dict[uuid.UUID, dict[date, StaffAttendance]] = {}
        if employees and end >= first:
            for r in (await self.session.execute(select(StaffAttendance).where(StaffAttendance.employee_id.in_([e.id for e in employees]),
                                                                                StaffAttendance.work_date >= first, StaffAttendance.work_date <= end))).scalars():
                rows.setdefault(r.employee_id, {})[r.work_date] = r
        out = {}
        for e in employees:
            t, mine = timing[e.id], rows.get(e.id, {})
            s = {"scheduled": 0, "present": 0, "excused": 0, "absent_dates": [], "late": 0, "early": 0, "missing_out": 0, "recorded": len(mine), "timing": t.out()}
            d = max(first, e.hire_date or first, started or first)
            while d <= end:
                r = mine.get(d)
                working = r.is_working_day if r is not None else d.weekday() in t.days
                if working:
                    if r is not None and (r.check_in_at or r.excused):
                        s["scheduled"] += 1
                        s["present"] += 1
                        if r.excused:
                            s["excused"] += 1
                        else:
                            s["late"] += 1 if r.late_minutes > 0 else 0
                            if r.check_out_at:
                                s["early"] += 1 if r.early_minutes > 0 else 0
                            elif d < today:
                                s["missing_out"] += 1
                    elif d < today:
                        s["scheduled"] += 1
                        s["absent_dates"].append(d)
                d += timedelta(days=1)
            s["absent"] = len(s["absent_dates"])
            out[e.id] = s
        return out

    async def month_report(self, month: date) -> list[dict]:
        people = (await self.session.execute(select(Employee, JobRole).join(JobRole, JobRole.id == Employee.job_role_id).where(Employee.status.in_(ACTIVE))
                                             .order_by(Employee.first_name, Employee.last_name))).all()
        sums = await self.summary(month, [e for e, _ in people])
        return [{"employee_id": e.id, "name": f"{e.first_name} {e.last_name}", "employee_code": e.employee_code, "job_title": r.title,
                 "is_teacher": r.default_role_code == "TEACHER", **{k: v for k, v in sums[e.id].items() if k != "absent_dates"},
                 "absent_dates": [d.isoformat() for d in sums[e.id]["absent_dates"]]} for e, r in people]

    async def employee_month(self, employee_id: uuid.UUID, month: date) -> dict:
        emp = await self._employee(employee_id)
        first, last = month_bounds(month)
        rows = (await self.session.execute(select(StaffAttendance).where(StaffAttendance.employee_id == emp.id, StaffAttendance.work_date >= first,
                                                                          StaffAttendance.work_date <= last).order_by(StaffAttendance.work_date))).scalars().all()
        s = (await self.summary(month, [emp]))[emp.id]
        return {"employee_id": emp.id, "name": f"{emp.first_name} {emp.last_name}", "employee_code": emp.employee_code,
                "summary": {**{k: v for k, v in s.items() if k != "absent_dates"}, "absent_dates": [d.isoformat() for d in s["absent_dates"]]},
                "days": [self._row_out(r) for r in rows]}