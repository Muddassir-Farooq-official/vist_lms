from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.auth.models import Role, User, UserRole
from app.auth.permissions import Roles
from app.core.clock import institute_today
from app.core.config import settings as app_settings
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError
from app.employees.models import Employee, EmployeeStatus, JobRole
from app.fines.models import Fine, FinePayerType, FineSource, FineStatus
from app.fines.service import FineService
from app.gate import rules
from app.gate.models import EmployeeSchedule, GateLog, GateSettings
from app.notifications.models import NotificationCategory
from app.notifications.service import create_notification
from app.programs.models import Program
from app.students.models import Student, StudentStatus

UNDO_MINUTES = 10                 # a guard may take back their own mistake for this long; the administration can always correct
STUDENT, EMPLOYEE = "STUDENT", "EMPLOYEE"
ACTIVE_EMPLOYEE = (EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE)


def _tz() -> ZoneInfo:
    return ZoneInfo(app_settings.institute_timezone)


def institute_now() -> datetime:
    """The current moment in the institute's timezone, WITH its timezone attached (so it can be compared with stored times)."""
    return datetime.now(_tz())


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


def _local(dt: datetime | None) -> datetime | None:
    return dt.astimezone(_tz()) if dt else None


def _name(o) -> str:
    return f"{o.first_name} {o.last_name}"


class GateService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ settings and timings
    async def settings(self) -> GateSettings:
        s = await self.session.get(GateSettings, 1)
        if s is None:
            s = GateSettings(id=1)
            self.session.add(s)
            await self.session.flush()
        return s

    @staticmethod
    def settings_out(s: GateSettings) -> dict:
        return {"student_in_time": s.student_in_time, "student_out_time": s.student_out_time, "student_grace_minutes": s.student_grace_minutes,
                "student_days": sorted(rules.parse_days(s.student_days)), "student_fine_per_hour": s.student_fine_per_hour, "student_max_fine_per_day": s.student_max_fine_per_day,
                "student_auto_fine": s.student_auto_fine, "staff_default_in": s.staff_default_in, "staff_default_out": s.staff_default_out,
                "staff_grace_minutes": s.staff_grace_minutes, "staff_default_days": sorted(rules.parse_days(s.staff_default_days))}

    async def update_settings(self, data: dict, *, admin_id: uuid.UUID) -> GateSettings:
        s = await self.settings()
        for key, value in data.items():
            setattr(s, key, rules.format_days(value) if key.endswith("_days") else value)
        s.updated_at = datetime.now(timezone.utc)
        await self.audit.log(actor_user_id=admin_id, action="gate.settings_updated", entity_type="gate_settings", entity_id=None)
        return s

    async def _schedules(self, employee_ids: list[uuid.UUID]) -> dict[uuid.UUID, EmployeeSchedule]:
        if not employee_ids:
            return {}
        return {x.employee_id: x for x in (await self.session.execute(select(EmployeeSchedule).where(EmployeeSchedule.employee_id.in_(employee_ids)))).scalars()}

    @staticmethod
    def _staff_expectation(s: GateSettings, sched: EmployeeSchedule | None, weekday: int) -> tuple[time | None, time | None, int, bool, bool]:
        """(expected in, expected out, grace, working today?, is the default)"""
        if sched is not None:
            return sched.in_time, sched.out_time, s.staff_grace_minutes, weekday in rules.parse_days(sched.working_days), False
        return s.staff_default_in, s.staff_default_out, s.staff_grace_minutes, weekday in rules.parse_days(s.staff_default_days), True

    @staticmethod
    def _student_expectation(s: GateSettings, weekday: int) -> tuple[time | None, time | None, int, bool]:
        return s.student_in_time, s.student_out_time, s.student_grace_minutes, weekday in rules.parse_days(s.student_days)

    async def list_schedules(self, *, q: str | None, job_role_id: uuid.UUID | None) -> list[dict]:
        s = await self.settings()
        stmt = select(Employee, JobRole).join(JobRole, JobRole.id == Employee.job_role_id).where(Employee.status.in_(ACTIVE_EMPLOYEE)).order_by(Employee.first_name, Employee.last_name)
        if job_role_id:
            stmt = stmt.where(Employee.job_role_id == job_role_id)
        rows = (await self.session.execute(stmt)).all()
        sched = await self._schedules([e.id for e, _ in rows])
        out = []
        for e, jr in rows:
            if q and q.strip() and q.strip().lower() not in f"{_name(e)} {e.employee_code} {jr.title}".lower():
                continue
            i, o, _g, _w, is_default = self._staff_expectation(s, sched.get(e.id), 0)
            days = rules.parse_days(sched[e.id].working_days) if e.id in sched else rules.parse_days(s.staff_default_days)
            out.append({"employee_id": e.id, "employee_code": e.employee_code, "name": _name(e), "job_title": jr.title, "job_role_id": jr.id, "in_time": i, "out_time": o,
                        "working_days": sorted(days), "days_text": rules.describe_days(days), "is_default": is_default})
        return out

    async def set_schedule(self, employee_id: uuid.UUID, data: dict, *, admin_id: uuid.UUID) -> None:
        if await self.session.get(Employee, employee_id) is None:
            raise ResourceNotFoundError("Employee not found.")
        await self._upsert(employee_id, data)
        await self.audit.log(actor_user_id=admin_id, action="gate.schedule_set", entity_type="employee", entity_id=employee_id)

    async def _upsert(self, employee_id: uuid.UUID, data: dict) -> None:
        row = (await self.session.execute(select(EmployeeSchedule).where(EmployeeSchedule.employee_id == employee_id))).scalars().first()
        days = rules.format_days(data["working_days"])
        if row is None:
            self.session.add(EmployeeSchedule(employee_id=employee_id, in_time=data["in_time"], out_time=data["out_time"], working_days=days))
        else:
            row.in_time, row.out_time, row.working_days = data["in_time"], data["out_time"], days

    async def clear_schedule(self, employee_id: uuid.UUID, *, admin_id: uuid.UUID) -> None:
        row = (await self.session.execute(select(EmployeeSchedule).where(EmployeeSchedule.employee_id == employee_id))).scalars().first()
        if row is not None:
            await self.session.delete(row)
            await self.audit.log(actor_user_id=admin_id, action="gate.schedule_cleared", entity_type="employee", entity_id=employee_id)

    async def bulk_schedule(self, data: dict, *, admin_id: uuid.UUID) -> int:
        stmt = select(Employee.id).where(Employee.status.in_(ACTIVE_EMPLOYEE))
        if data["scope"] == "JOB_ROLE":
            stmt = stmt.where(Employee.job_role_id == data["job_role_id"])
        ids = list((await self.session.execute(stmt)).scalars())
        for eid in ids:
            await self._upsert(eid, data)
        await self.audit.log(actor_user_id=admin_id, action="gate.schedule_bulk", entity_type="employee", entity_id=None, metadata={"people": len(ids), "scope": data["scope"]})
        return len(ids)

    # ------------------------------------------------------------------ people, names and today's state
    async def _info(self, pairs: set[tuple[str, uuid.UUID]]) -> dict[tuple[str, uuid.UUID], dict]:
        out: dict = {}
        sids = [i for k, i in pairs if k == STUDENT]
        eids = [i for k, i in pairs if k == EMPLOYEE]
        if sids:
            for st, code in (await self.session.execute(select(Student, Program.code).outerjoin(Program, Program.id == Student.program_id).where(Student.id.in_(sids)))).all():
                out[(STUDENT, st.id)] = {"name": _name(st), "identifier": st.roll_number, "group": (code or "") + (f" \u00b7 Semester {st.current_semester}" if st.current_semester else "") or None}
        if eids:
            for em, title in (await self.session.execute(select(Employee, JobRole.title).join(JobRole, JobRole.id == Employee.job_role_id).where(Employee.id.in_(eids)))).all():
                out[(EMPLOYEE, em.id)] = {"name": _name(em), "identifier": em.employee_code, "group": title}
        return out

    async def _user_names(self, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        user_ids.discard(None)
        out: dict = {}
        if not user_ids:
            return out
        for em in (await self.session.execute(select(Employee).where(Employee.user_id.in_(user_ids)))).scalars():
            out[em.user_id] = _name(em)
        for u in (await self.session.execute(select(User).where(User.id.in_(user_ids - set(out))))).scalars():
            out[u.id] = u.username
        return out

    async def _fines(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, Fine]:
        ids = [i for i in ids if i]
        return {f.id: f for f in (await self.session.execute(select(Fine).where(Fine.id.in_(ids)))).scalars()} if ids else {}

    @staticmethod
    def _fine_brief(f: Fine | None) -> dict | None:
        return {"id": f.id, "amount": f.amount, "status": f.status.value} if f else None

    @staticmethod
    def _can_undo(log: GateLog | None, guard_id: uuid.UUID | None, admin: bool, now: datetime) -> bool:
        if log is None or log.check_in_at is None:
            return False
        if admin:
            return True
        marker, at = (log.out_marked_by, log.check_out_at) if log.check_out_at else (log.in_marked_by, log.check_in_at)
        return bool(guard_id and marker == guard_id and at and now - at <= timedelta(minutes=UNDO_MINUTES))

    def _card(self, kind: str, person_id: uuid.UUID, info: dict, log: GateLog | None, exp: tuple, fine: Fine | None, *, guard_id, admin: bool, now: datetime) -> dict:
        exp_in, exp_out, _grace, working = exp[:4]
        inside = bool(log and log.check_in_at and not log.check_out_at)
        left = bool(log and log.check_out_at)
        return {"kind": kind, "person_id": person_id, "name": info["name"], "identifier": info["identifier"], "group": info["group"], "expected_in": exp_in, "expected_out": exp_out,
                "working_today": working, "state": "LEFT" if left else "INSIDE" if inside else "NOT_ARRIVED", "check_in_at": log.check_in_at if log else None,
                "check_out_at": log.check_out_at if log else None, "late_minutes": log.late_minutes if log else 0, "early_minutes": log.early_minutes if log else 0,
                "log_id": log.id if log else None, "can_check_in": log is None, "can_check_out": inside, "can_undo": self._can_undo(log, guard_id, admin, now), "fine": self._fine_brief(fine)}

    async def _todays_logs(self, today: date) -> dict[tuple[str, uuid.UUID], GateLog]:
        return {(g.kind, g.person_id): g for g in (await self.session.execute(select(GateLog).where(GateLog.log_date == today))).scalars()}

    async def filters(self) -> dict:
        progs = (await self.session.execute(select(Program.id, Program.code).order_by(Program.code))).all()
        top = (await self.session.execute(select(func.max(Student.current_semester)).where(Student.status == StudentStatus.ACTIVE))).scalar_one() or 8
        roles = (await self.session.execute(select(JobRole.id, JobRole.title).where(JobRole.is_active.is_(True)).order_by(JobRole.title))).all()
        return {"programs": [{"id": i, "code": c} for i, c in progs], "semesters": list(range(1, max(top, 1) + 1)), "job_roles": [{"id": i, "title": t} for i, t in roles]}

    # ------------------------------------------------------------------ searching
    async def search(self, *, q: str | None, kind: str | None, program_id: uuid.UUID | None, semester: int | None, job_role_id: uuid.UUID | None, state: str | None,
                     limit: int, offset: int, guard_id: uuid.UUID, admin: bool) -> dict:
        s, now = await self.settings(), institute_now()
        today, weekday = now.date(), now.weekday()
        logs = await self._todays_logs(today)
        terms = [t for t in (q or "").replace(",", " ").split() if t]
        limit = max(1, min(limit, 100))
        want_students = kind in (None, "", STUDENT) and job_role_id is None
        want_staff = kind in (None, "", EMPLOYEE) and program_id is None and semester is None

        def by_state(ids: list[uuid.UUID], kind_name: str, column):
            if state == "NOT_ARRIVED":
                taken = [i for (k, i) in logs if k == kind_name]
                return column.not_in(taken) if taken else None
            pick = {"INSIDE": lambda g: g.check_in_at and not g.check_out_at, "LEFT": lambda g: g.check_out_at, "LATE": lambda g: g.late_minutes > 0}.get(state or "")
            if pick is None:
                return None
            return column.in_([i for (k, i), g in logs.items() if k == kind_name and pick(g)] or [uuid.UUID(int=0)])

        cards, more = [], False
        if want_students:
            stmt = select(Student, Program.code).outerjoin(Program, Program.id == Student.program_id).where(Student.status == StudentStatus.ACTIVE)
            for t in terms:
                stmt = stmt.where(or_(Student.first_name.ilike(f"%{t}%"), Student.last_name.ilike(f"%{t}%"), Student.roll_number.ilike(f"%{t}%")))
            if program_id:
                stmt = stmt.where(Student.program_id == program_id)
            if semester:
                stmt = stmt.where(Student.current_semester == semester)
            cond = by_state([], STUDENT, Student.id)
            if cond is not None:
                stmt = stmt.where(cond)
            rows = (await self.session.execute(stmt.order_by(Student.first_name, Student.last_name).offset(offset).limit(limit + 1))).all()
            more = more or len(rows) > limit
            fines = await self._fines([logs[(STUDENT, st.id)].fine_id for st, _ in rows[:limit] if (STUDENT, st.id) in logs])
            exp = self._student_expectation(s, weekday)
            for st, code in rows[:limit]:
                g = logs.get((STUDENT, st.id))
                info = {"name": _name(st), "identifier": st.roll_number, "group": (code or "") + (f" \u00b7 Semester {st.current_semester}" if st.current_semester else "") or None}
                cards.append(self._card(STUDENT, st.id, info, g, exp, fines.get(g.fine_id) if g else None, guard_id=guard_id, admin=admin, now=now))
        if want_staff:
            stmt = select(Employee, JobRole.title).join(JobRole, JobRole.id == Employee.job_role_id).where(Employee.status.in_(ACTIVE_EMPLOYEE))
            for t in terms:
                stmt = stmt.where(or_(Employee.first_name.ilike(f"%{t}%"), Employee.last_name.ilike(f"%{t}%"), Employee.employee_code.ilike(f"%{t}%")))
            if job_role_id:
                stmt = stmt.where(Employee.job_role_id == job_role_id)
            cond = by_state([], EMPLOYEE, Employee.id)
            if cond is not None:
                stmt = stmt.where(cond)
            rows = (await self.session.execute(stmt.order_by(Employee.first_name, Employee.last_name).offset(offset).limit(limit + 1))).all()
            more = more or len(rows) > limit
            sched = await self._schedules([e.id for e, _ in rows[:limit]])
            for em, title in rows[:limit]:
                g = logs.get((EMPLOYEE, em.id))
                exp = self._staff_expectation(s, sched.get(em.id), weekday)
                cards.append(self._card(EMPLOYEE, em.id, {"name": _name(em), "identifier": em.employee_code, "group": title}, g, exp, None, guard_id=guard_id, admin=admin, now=now))
        cards.sort(key=lambda c: c["name"].lower())
        return {"people": cards, "has_more": more}

    async def card(self, kind: str, person_id: uuid.UUID, *, guard_id: uuid.UUID, admin: bool) -> dict:
        s, now = await self.settings(), institute_now()
        info = (await self._info({(kind, person_id)})).get((kind, person_id))
        if info is None:
            raise ResourceNotFoundError("Person not found.")
        log = (await self.session.execute(select(GateLog).where(GateLog.kind == kind, GateLog.person_id == person_id, GateLog.log_date == now.date()))).scalars().first()
        if kind == STUDENT:
            exp = self._student_expectation(s, now.weekday())
        else:
            exp = self._staff_expectation(s, (await self._schedules([person_id])).get(person_id), now.weekday())
        fine = (await self._fines([log.fine_id])).get(log.fine_id) if log and log.fine_id else None
        return self._card(kind, person_id, info, log, exp, fine, guard_id=guard_id, admin=admin, now=now)

    async def _load(self, kind: str, person_id: uuid.UUID):
        if kind == STUDENT:
            o = await self.session.get(Student, person_id)
            if o is None or o.status != StudentStatus.ACTIVE:
                raise BusinessRuleViolationError("This student is not an active student, so they cannot be marked in.")
        else:
            o = await self.session.get(Employee, person_id)
            if o is None or o.status not in ACTIVE_EMPLOYEE:
                raise BusinessRuleViolationError("This person is not an active member of staff, so they cannot be marked in.")
        return o

    # ------------------------------------------------------------------ marking in and out
    async def check_in(self, *, guard_id: uuid.UUID, kind: str, person_id: uuid.UUID, admin: bool) -> dict:
        s, now = await self.settings(), institute_now()
        today = now.date()
        person = await self._load(kind, person_id)
        name = _name(person)
        existing = (await self.session.execute(select(GateLog).where(GateLog.kind == kind, GateLog.person_id == person_id, GateLog.log_date == today))).scalars().first()
        if existing is not None:
            raise BusinessRuleViolationError(f"{name} is already marked in today (at {_local(existing.check_in_at):%H:%M}).")
        if kind == STUDENT:
            exp_in, exp_out, grace, working = self._student_expectation(s, now.weekday())
        else:
            exp_in, exp_out, grace, working, _d = self._staff_expectation(s, (await self._schedules([person_id])).get(person_id), now.weekday())
        late = rules.late_minutes(now, exp_in, grace, working)
        log = GateLog(kind=kind, person_id=person_id, log_date=today, check_in_at=_utc(now), expected_in=exp_in, expected_out=exp_out, grace_minutes=grace, is_working_day=working,
                      late_minutes=late, in_marked_by=guard_id)
        self.session.add(log)
        await self.session.flush()
        if kind == STUDENT and late > 0:
            await self._sync_fine(log, person, s)
        await self.audit.log(actor_user_id=guard_id, action="gate.check_in", entity_type="gate_log", entity_id=log.id, metadata={"kind": kind, "late_minutes": late})
        return await self.card(kind, person_id, guard_id=guard_id, admin=admin)

    async def check_out(self, *, guard_id: uuid.UUID, kind: str, person_id: uuid.UUID, admin: bool) -> dict:
        now = institute_now()
        person = await self._load(kind, person_id)
        log = (await self.session.execute(select(GateLog).where(GateLog.kind == kind, GateLog.person_id == person_id, GateLog.log_date == now.date()))).scalars().first()
        if log is None or log.check_in_at is None:
            raise BusinessRuleViolationError(f"{_name(person)} has not been marked in today. Mark them in first.")
        if log.check_out_at is not None:
            raise BusinessRuleViolationError(f"{_name(person)} was already marked out today (at {_local(log.check_out_at):%H:%M}).")
        log.check_out_at, log.out_marked_by = _utc(now), guard_id
        log.early_minutes = rules.early_minutes(now, log.expected_out, log.grace_minutes, log.is_working_day)
        await self.audit.log(actor_user_id=guard_id, action="gate.check_out", entity_type="gate_log", entity_id=log.id, metadata={"kind": kind, "early_minutes": log.early_minutes})
        return await self.card(kind, person_id, guard_id=guard_id, admin=admin)

    async def undo(self, log_id: uuid.UUID, *, guard_id: uuid.UUID, admin: bool) -> dict | None:
        """Take back the latest mark: the check-out if there is one, otherwise the whole entry (and its fine request, if still undecided)."""
        log = await self._get(log_id)
        if not self._can_undo(log, guard_id, admin, institute_now()):
            raise AuthorizationError(f"You can only undo your own mark, and only within {UNDO_MINUTES} minutes. Ask the administration to correct it.")
        kind, person_id = log.kind, log.person_id
        if log.check_out_at is not None:
            log.check_out_at, log.out_marked_by, log.early_minutes = None, None, 0
        else:
            await self._drop(log)
        await self.audit.log(actor_user_id=guard_id, action="gate.undo", entity_type="gate_log", entity_id=log_id)
        return await self.card(kind, person_id, guard_id=guard_id, admin=admin)

    async def _get(self, log_id: uuid.UUID) -> GateLog:
        log = await self.session.get(GateLog, log_id)
        if log is None:
            raise ResourceNotFoundError("Entry not found.")
        return log

    async def _drop(self, log: GateLog) -> None:
        fine = await self.session.get(Fine, log.fine_id) if log.fine_id else None
        if fine is not None:
            if fine.status != FineStatus.PENDING_APPROVAL:
                raise BusinessRuleViolationError("A fine for this day has already been decided, so the entry cannot be removed. Ask the administration.")
            log.fine_id = None
            await self.session.flush()
            await self.session.delete(fine)
        await self.session.delete(log)

    # ------------------------------------------------------------------ the late-arrival fine (always a REQUEST: the administration decides)
    async def _admin_ids(self) -> list[uuid.UUID]:
        return list(set((await self.session.execute(
            select(User.id).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id).where(Role.code == Roles.ADMIN, User.is_active.is_(True))
        )).scalars()))

    async def _sync_fine(self, log: GateLog, student: Student, s: GateSettings) -> str | None:
        """Make the fine request match the lateness. Returns a warning when a fine that was already decided cannot be changed here."""
        amount = rules.student_fine(log.late_minutes, s.student_fine_per_hour, s.student_max_fine_per_day) if (log.late_minutes > 0 and s.student_auto_fine) else Decimal(0)
        fine = await self.session.get(Fine, log.fine_id) if log.fine_id else None
        arrived = _local(log.check_in_at)
        reason = (f"Late arrival on {log.log_date:%d %b %Y}: arrived {arrived:%H:%M}, {rules.human(log.late_minutes)} after {log.expected_in:%H:%M} "
                  f"(Rs. {Decimal(str(s.student_fine_per_hour)):g} per hour, every started hour counts)") if log.late_minutes else ""
        if fine is not None and fine.status != FineStatus.PENDING_APPROVAL:
            return "A fine for this day has already been decided. Adjust it in Fines if the times changed."
        if fine is not None:
            if amount > 0:
                fine.amount, fine.reason = amount, reason[:500]
            else:
                log.fine_id = None
                await self.session.flush()
                await self.session.delete(fine)
            return None
        if amount > 0:
            fine = Fine(payer_type=FinePayerType.STUDENT, payer_id=student.id, amount=amount, reason=reason[:500], source=FineSource.GATE, status=FineStatus.PENDING_APPROVAL,
                        issued_by=None, issued_at=datetime.now(timezone.utc), related_entity_type="gate_log", related_entity_id=log.id)
            self.session.add(fine)
            await self.session.flush()
            log.fine_id = fine.id
            for admin_id in await self._admin_ids():
                await create_notification(self.session, user_id=admin_id, category=NotificationCategory.FINE, title="Late-arrival fine waiting for approval",
                                          message=f"{_name(student)} ({student.roll_number}) arrived {rules.human(log.late_minutes)} late. Suggested fine: Rs. {amount:,.2f}.",
                                          link="/dashboard/admin/gate?open=fines")
        return None

    # ------------------------------------------------------------------ the log
    async def logs(self, *, day: date, kind: str | None, late_only: bool, q: str | None, limit: int, offset: int, guard_id: uuid.UUID, admin: bool) -> list[dict]:
        stmt = select(GateLog).where(GateLog.log_date == day).order_by(GateLog.check_in_at.desc())
        if kind:
            stmt = stmt.where(GateLog.kind == kind)
        if late_only:
            stmt = stmt.where(GateLog.late_minutes > 0)
        rows = list((await self.session.execute(stmt.limit(3000))).scalars())
        info = await self._info({(g.kind, g.person_id) for g in rows})
        fines = await self._fines([g.fine_id for g in rows])
        names = await self._user_names({g.in_marked_by for g in rows} | {g.out_marked_by for g in rows})
        now, out = institute_now(), []
        for g in rows:
            i = info.get((g.kind, g.person_id), {"name": "Removed", "identifier": None, "group": None})
            if q and q.strip():
                hay = f"{i['name']} {i['identifier'] or ''}".lower()
                if not all(t in hay for t in q.lower().split()):
                    continue
            out.append({"id": g.id, "kind": g.kind, "person_id": g.person_id, "name": i["name"], "identifier": i["identifier"], "group": i["group"], "log_date": g.log_date,
                        "check_in_at": g.check_in_at, "check_out_at": g.check_out_at, "expected_in": g.expected_in, "expected_out": g.expected_out, "late_minutes": g.late_minutes,
                        "early_minutes": g.early_minutes, "is_working_day": g.is_working_day, "in_marked_by": names.get(g.in_marked_by), "out_marked_by": names.get(g.out_marked_by),
                        "note": g.note, "fine": self._fine_brief(fines.get(g.fine_id)), "can_undo": self._can_undo(g, guard_id, admin, now)})
        return out[max(0, offset): max(0, offset) + max(1, min(limit, 500))]

    async def summary(self) -> dict:
        s, now = await self.settings(), institute_now()
        today = now.date()
        logs = list((await self.session.execute(select(GateLog).where(GateLog.log_date == today))).scalars())
        st, sf = [g for g in logs if g.kind == STUDENT], [g for g in logs if g.kind == EMPLOYEE]
        inside = lambda gs: sum(1 for g in gs if g.check_in_at and not g.check_out_at)           # noqa: E731
        staff = list((await self.session.execute(select(Employee).where(Employee.status == EmployeeStatus.ACTIVE))).scalars())
        sched = await self._schedules([e.id for e in staff])
        in_ids = {g.person_id for g in sf}
        expected_missing = sum(1 for e in staff if e.id not in in_ids and self._staff_expectation(s, sched.get(e.id), now.weekday())[3])
        return {"date": today, "inside_students": inside(st), "inside_staff": inside(sf), "checked_in_students": len(st), "checked_in_staff": len(sf),
                "late_students": sum(1 for g in st if g.late_minutes > 0), "late_staff": sum(1 for g in sf if g.late_minutes > 0),
                "left_today": sum(1 for g in logs if g.check_out_at), "staff_not_arrived": expected_missing}

    # ------------------------------------------------------------------ the administration corrects the record
    async def edit_log(self, log_id: uuid.UUID, *, admin_id: uuid.UUID, check_in_time: time | None, check_out_time: time | None, clear_check_out: bool, note: str | None) -> dict:
        log, s = await self._get(log_id), await self.settings()
        combine = lambda t: _utc(datetime.combine(log.log_date, t, tzinfo=_tz()))                    # noqa: E731
        if check_in_time is not None:
            log.check_in_at = combine(check_in_time)
        if clear_check_out:
            log.check_out_at, log.early_minutes = None, 0
        elif check_out_time is not None:
            log.check_out_at = combine(check_out_time)
        if log.check_in_at and log.check_out_at and log.check_out_at < log.check_in_at:
            raise BusinessRuleViolationError("The leaving time cannot be before the arrival time.")
        if log.check_in_at:
            log.late_minutes = rules.late_minutes(_local(log.check_in_at), log.expected_in, log.grace_minutes, log.is_working_day)
        if log.check_out_at:
            log.early_minutes = rules.early_minutes(_local(log.check_out_at), log.expected_out, log.grace_minutes, log.is_working_day)
        if note is not None:
            log.note = note.strip() or None
        warning = None
        if log.kind == STUDENT:
            student = await self.session.get(Student, log.person_id)
            if student is not None:
                warning = await self._sync_fine(log, student, s)
        await self.audit.log(actor_user_id=admin_id, action="gate.log_corrected", entity_type="gate_log", entity_id=log.id)
        await self.session.flush()
        row = (await self.logs(day=log.log_date, kind=log.kind, late_only=False, q=None, limit=500, offset=0, guard_id=admin_id, admin=True))
        out = next(r for r in row if r["id"] == log.id)
        out["warning"] = warning
        return out

    async def delete_log(self, log_id: uuid.UUID, *, admin_id: uuid.UUID) -> None:
        log = await self._get(log_id)
        await self._drop(log)
        await self.audit.log(actor_user_id=admin_id, action="gate.log_deleted", entity_type="gate_log", entity_id=log_id)

    # ------------------------------------------------------------------ fines the administration decides
    async def fines(self, status: str | None) -> list[dict]:
        s = await self.settings()
        stmt = select(Fine, GateLog).outerjoin(GateLog, GateLog.fine_id == Fine.id).where(Fine.source == FineSource.GATE).order_by(Fine.issued_at.desc())
        if status == "PENDING_APPROVAL":
            stmt = stmt.where(Fine.status == FineStatus.PENDING_APPROVAL)
        elif status == "APPROVED":
            stmt = stmt.where(Fine.status.in_([FineStatus.PENDING, FineStatus.PAID, FineStatus.WAIVED]))
        elif status == "REJECTED":
            stmt = stmt.where(Fine.status == FineStatus.REJECTED)
        rows = (await self.session.execute(stmt.limit(500))).all()
        info = await self._info({(STUDENT, f.payer_id) for f, _ in rows})
        out = []
        for f, g in rows:
            i = info.get((STUDENT, f.payer_id), {"name": "Unknown", "identifier": None, "group": None})
            late = g.late_minutes if g else 0
            out.append({"fine_id": f.id, "student_id": f.payer_id, "name": i["name"], "roll_number": i["identifier"], "group": i["group"], "log_id": g.id if g else None,
                        "log_date": g.log_date if g else None, "arrived_at": g.check_in_at if g else None, "expected_in": g.expected_in if g else None, "late_minutes": late,
                        "hours": rules.late_hours(late), "amount": f.amount, "suggested_amount": rules.student_fine(late, s.student_fine_per_hour, s.student_max_fine_per_day) if late else None,
                        "status": f.status.value, "reason": f.reason, "decision_note": f.resolution_note, "decided_at": f.approved_at or f.resolved_at})
        return out

    async def decide_fine(self, fine_id: uuid.UUID, *, admin_id: uuid.UUID, approve: bool, amount: Decimal | None, note: str | None) -> dict:
        fine = await self.session.get(Fine, fine_id)
        if fine is None or fine.source != FineSource.GATE:
            raise ResourceNotFoundError("Fine not found.")
        fines = FineService(self.session)
        if approve:
            if amount is not None and Decimal(str(fine.amount)) != amount:
                await self.audit.log(actor_user_id=admin_id, action="gate.fine_amount_changed", entity_type="fine", entity_id=fine.id,
                                     before={"amount": str(fine.amount)}, after={"amount": str(amount)})
                fine.amount = amount
            await fines.approve(fine.id, actor_id=admin_id, note=(note or None))
        else:
            await fines.reject(fine.id, actor_id=admin_id, note=(note or "").strip())
        return next(r for r in await self.fines("ALL") if r["fine_id"] == fine.id)

    # ------------------------------------------------------------------ who is late, how often, and what it cost
    async def report(self, days: int, kind: str | None) -> dict:
        today = institute_today()
        since = today - timedelta(days=days) if days > 0 else None
        stmt = select(GateLog).where(GateLog.check_in_at.is_not(None))
        if since:
            stmt = stmt.where(GateLog.log_date >= since)
        if kind:
            stmt = stmt.where(GateLog.kind == kind)
        logs = list((await self.session.execute(stmt.limit(100000))).scalars())
        info = await self._info({(g.kind, g.person_id) for g in logs if g.late_minutes > 0})
        fines = await self._fines([g.fine_id for g in logs])
        per: dict = {}
        approved = pending = Decimal(0)
        for g in logs:
            if g.late_minutes <= 0:
                continue
            row = per.setdefault((g.kind, g.person_id), {"late_days": 0, "total": 0, "longest": 0, "ok": Decimal(0), "wait": Decimal(0)})
            row["late_days"] += 1; row["total"] += g.late_minutes; row["longest"] = max(row["longest"], g.late_minutes)
            f = fines.get(g.fine_id)
            if f is not None:
                amt = Decimal(str(f.amount))
                if f.status == FineStatus.PENDING_APPROVAL:
                    row["wait"] += amt; pending += amt
                elif f.status in (FineStatus.PENDING, FineStatus.PAID):
                    row["ok"] += amt; approved += amt
        present_by = {}
        for g in logs:
            present_by[(g.kind, g.person_id)] = present_by.get((g.kind, g.person_id), 0) + 1
        people = [{"kind": k[0], "person_id": k[1], "name": info.get(k, {"name": "Removed"})["name"], "identifier": info.get(k, {}).get("identifier"), "group": info.get(k, {}).get("group"),
                   "days_present": present_by.get(k, 0), "late_days": v["late_days"], "total_late_minutes": v["total"], "average_late_minutes": v["total"] // v["late_days"],
                   "longest_late_minutes": v["longest"], "fines_approved": v["ok"], "fines_pending": v["wait"]} for k, v in per.items()]
        people.sort(key=lambda r: (-r["late_days"], -r["total_late_minutes"]))
        monday = today - timedelta(days=today.weekday())
        weeks = {monday - timedelta(weeks=i): [0, 0] for i in range(11, -1, -1)}
        for g in logs:
            wk = g.log_date - timedelta(days=g.log_date.weekday())
            if wk in weeks:
                weeks[wk][1] += 1
                weeks[wk][0] += g.late_minutes > 0
        late_days = sum(1 for g in logs if g.late_minutes > 0)
        return {"days": days, "since": since, "present_days": len(logs), "late_days": late_days, "late_rate": round(late_days / len(logs) * 100, 1) if logs else None,
                "total_late_minutes": sum(g.late_minutes for g in logs), "fines_approved": approved, "fines_pending": pending,
                "weeks": [{"week_start": wk, "late": v[0], "present": v[1]} for wk, v in weeks.items()], "people": people[:300]}