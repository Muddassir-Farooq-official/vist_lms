"""Warnings to students and staff.

* Only the admin issues a warning (permission warning.issue): a violation from the dropdown, the date, and a note.
* A warning COUNTS for 12 months (COUNT_DAYS) unless withdrawn. The one that brings the count to 3 (LIMIT) is FINAL:
  it waits for the admin or principal (warning.decide) to choose the action, which is applied to the person's record:
      students:  NONE (final chance) | SUSPEND (status Suspended: sign-in blocked) | EXPEL (status Dropped) | FINE (fee account)
      staff:     NONE | SUSPEND (status On leave and sign-in blocked) | TERMINATE (status Terminated) | FINE (from salary)
  A suspension is lifted from the same screen (the person is active again). After a final chance the count goes on: the
  next warning is final again. While a final warning waits for a decision, no further warning can be issued to that person.
* The person is told in the portal, sees every warning on their "My warnings" page, acknowledges it ("I have read this",
  recorded with the time) and can write one reply (appeal). A message to a student's guardian is off unless switched on
  (Settings, sms_on_warning). The admin or principal can withdraw a warning (it then stops counting; it stays on record).
Nothing is ever deleted.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.clock import institute_today
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.discipline.models import DisciplinaryWarning, ViolationType

LIMIT = 3
COUNT_DAYS = 365
ACTIONS = {"STUDENT": ("NONE", "SUSPEND", "EXPEL", "FINE"), "EMPLOYEE": ("NONE", "SUSPEND", "TERMINATE", "FINE")}
ACTION_TEXT = {"PENDING": "Waiting for a decision", "NONE": "Final chance (no action)", "SUSPEND": "Suspended", "EXPEL": "Expelled",
               "TERMINATE": "Terminated", "FINE": "Fined"}
MAX_FINE = 1_000_000
MY_PAGE = "/dashboard/my-warnings"
OFFICE_PAGE = "/dashboard/admin/warnings"
PRINCIPAL_PAGE = "/dashboard/principal/warnings"

DEFAULT_TYPES = {
    "STUDENT": ["Absent without leave", "Late to class again and again", "Misbehaviour with a teacher or staff member", "Fighting or bullying",
                "Cheating or unfair means in an exam", "Damage to institute property", "Not following the dress code",
                "Using a mobile phone in class", "Harassment", "Smoking or drugs on campus", "Other"],
    "EMPLOYEE": ["Late arrival or leaving early", "Absent without approved leave", "Negligence of duty",
                 "Misconduct with students, parents or colleagues", "Not following instructions (insubordination)", "Harassment",
                 "Misuse of institute property or funds", "Breach of confidentiality", "Not following institute policy", "Other"],
}


def _kind(w: DisciplinaryWarning) -> str:
    return "STUDENT" if w.student_id else "EMPLOYEE"


def reference(w: DisciplinaryWarning) -> str:
    return f"WRN-{(w.created_at or datetime.now(timezone.utc)):%Y}-{w.serial:04d}"


def _since() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=COUNT_DAYS)


def counts_now(w: DisciplinaryWarning) -> bool:
    return w.status == "ACTIVE" and (w.created_at is None or w.created_at >= _since())


class WarningService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ violation types
    async def types(self, kind: str | None = None, *, include_off: bool = False) -> list[dict]:
        if (await self.session.execute(select(func.count()).select_from(ViolationType))).scalar_one() == 0:
            for k, names in DEFAULT_TYPES.items():                       # the first time: a ready-made list to start from
                for i, n in enumerate(names):
                    self.session.add(ViolationType(applies_to=k, name=n, sort_order=(i + 1) * 10 if n != "Other" else 999))
            await self.session.flush()
        stmt = select(ViolationType)
        if kind:
            stmt = stmt.where(ViolationType.applies_to.in_([kind, "BOTH"]))
        if not include_off:
            stmt = stmt.where(ViolationType.is_active.is_(True))
        rows = (await self.session.execute(stmt.order_by(ViolationType.applies_to, ViolationType.sort_order, ViolationType.name))).scalars().all()
        return [{"id": t.id, "applies_to": t.applies_to, "name": t.name, "is_active": t.is_active, "sort_order": t.sort_order} for t in rows]

    async def save_type(self, *, actor_id, type_id: uuid.UUID | None = None, applies_to: str | None = None, name: str | None = None,
                        is_active: bool | None = None) -> ViolationType:
        if type_id is None:
            name = " ".join((name or "").split())
            if applies_to not in ("STUDENT", "EMPLOYEE", "BOTH") or len(name) < 3:
                raise ValidationAppError("Choose who it is for and give a name (at least 3 letters).")
            dup = (await self.session.execute(select(ViolationType).where(ViolationType.applies_to == applies_to,
                                                                          func.lower(ViolationType.name) == name.lower()))).scalars().first()
            if dup is not None:
                raise BusinessRuleViolationError("That violation is already in the list.")
            t = ViolationType(applies_to=applies_to, name=name[:120], sort_order=500)
            self.session.add(t)
        else:
            t = await self.session.get(ViolationType, type_id)
            if t is None:
                raise ResourceNotFoundError("Violation not found.")
            if name is not None and len(" ".join(name.split())) >= 3:
                t.name = " ".join(name.split())[:120]
            if is_active is not None:
                t.is_active = is_active
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="warning.violation_type_saved", entity_type="violation_type", entity_id=t.id,
                             after={"name": t.name, "applies_to": t.applies_to, "is_active": t.is_active})
        return t

    # ------------------------------------------------------------------ people
    async def _person(self, kind: str, person_id: uuid.UUID):
        if kind == "STUDENT":
            from app.students.models import Student

            p = await self.session.get(Student, person_id)
            if p is None:
                raise ResourceNotFoundError("Student not found.")
            return p
        if kind == "EMPLOYEE":
            from app.employees.models import Employee

            p = await self.session.get(Employee, person_id)
            if p is None:
                raise ResourceNotFoundError("Employee not found.")
            return p
        raise ValidationAppError("Choose a student or a staff member.")

    @staticmethod
    def _describe(kind: str, p) -> dict:
        if kind == "STUDENT":
            return {"kind": kind, "id": p.id, "name": f"{p.first_name} {p.last_name}".strip(), "code": p.roll_number, "status": p.status.value,
                    "user_id": p.user_id}
        return {"kind": kind, "id": p.id, "name": f"{p.first_name} {p.last_name}".strip(), "code": p.employee_code, "status": p.status.value,
                "user_id": p.user_id}

    async def search(self, kind: str, q: str, limit: int = 12) -> list[dict]:
        q = " ".join((q or "").split())
        if len(q) < 2:
            return []
        like = f"%{q}%"
        if kind == "STUDENT":
            from app.students.models import Student as M
            code = M.roll_number
        else:
            from app.employees.models import Employee as M
            code = M.employee_code
        rows = (await self.session.execute(select(M).where(or_(code.ilike(like), func.concat(M.first_name, " ", M.last_name).ilike(like), M.cnic.ilike(like)))
                                           .order_by(M.first_name).limit(limit))).scalars().all()
        out = []
        for p in rows:
            d = self._describe(kind, p)
            d["counting"] = await self._counting(kind, p.id)
            d.pop("user_id", None)
            out.append(d)
        return out

    async def _counting(self, kind: str, person_id: uuid.UUID) -> int:
        col = DisciplinaryWarning.student_id if kind == "STUDENT" else DisciplinaryWarning.employee_id
        return (await self.session.execute(select(func.count()).select_from(DisciplinaryWarning).where(
            col == person_id, DisciplinaryWarning.status == "ACTIVE", DisciplinaryWarning.created_at >= _since()))).scalar_one()

    # ------------------------------------------------------------------ notifying
    async def _tell(self, user_ids, title: str, message: str, link: str) -> None:
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification

        for uid in {u for u in user_ids if u}:
            await create_notification(self.session, user_id=uid, category=NotificationCategory.GENERAL, title=title, message=message, link=link)

    async def _deciders(self) -> list:
        from app.approvals.service import _users_with_permission
        from app.auth.permissions import Permissions

        return list(await _users_with_permission(self.session, Permissions.WARNING_DECIDE))

    async def _text_student(self, student_id, body: str) -> None:
        if not int(getattr(settings, "sms_on_warning", 1)):
            return
        try:
            from app.sms.service import SmsService

            async with self.session.begin_nested():
                await SmsService(self.session).queue_to_guardian(student_id, event="WARNING", body=f"{settings.app_name}: {body}"[:300])
        except Exception:  # noqa: BLE001 - a texting problem must not undo the warning
            pass

    # ------------------------------------------------------------------ issuing
    async def issue(self, *, kind: str, person_id: uuid.UUID, violation_type_id: uuid.UUID, note: str, incident_date: date | None, actor_id) -> DisciplinaryWarning:
        kind = (kind or "").upper()
        p = await self._person(kind, person_id)
        who = self._describe(kind, p)
        if who["status"] in ("GRADUATED", "DROPPED", "TERMINATED"):
            raise BusinessRuleViolationError(f"{who['name']} is no longer {'a student' if kind == 'STUDENT' else 'working'} here.")
        t = await self.session.get(ViolationType, violation_type_id)
        if t is None or not t.is_active or t.applies_to not in (kind, "BOTH"):
            raise ValidationAppError("Choose a violation from the list.")
        note = (note or "").strip()
        if len(note) < 10:
            raise ValidationAppError("Write what happened (at least 10 characters). The person will read it.")
        day = incident_date or institute_today()
        if day > institute_today() or day < institute_today() - timedelta(days=COUNT_DAYS):
            raise ValidationAppError("The date must be within the last 12 months, and not in the future.")
        col = DisciplinaryWarning.student_id if kind == "STUDENT" else DisciplinaryWarning.employee_id
        pending = (await self.session.execute(select(func.count()).select_from(DisciplinaryWarning).where(
            col == person_id, DisciplinaryWarning.status == "ACTIVE", DisciplinaryWarning.action == "PENDING"))).scalar_one()
        if pending:
            raise BusinessRuleViolationError(f"{who['name']} already has a final warning waiting for a decision. Decide on it first.")
        number = await self._counting(kind, person_id) + 1
        final = number >= LIMIT
        w = DisciplinaryWarning(student_id=person_id if kind == "STUDENT" else None, employee_id=person_id if kind == "EMPLOYEE" else None,
                                violation_type_id=t.id, violation=t.name, note=note[:4000], incident_date=day, number=number, is_final=final,
                                status="ACTIVE", issued_by=actor_id, action="PENDING" if final else None)
        self.session.add(w)
        await self.session.flush()
        await self.session.refresh(w)
        await self.audit.log(actor_user_id=actor_id, action="warning.issued", entity_type="disciplinary_warning", entity_id=w.id,
                             after={"person": who["code"], "kind": kind, "violation": t.name, "number": number, "final": final})
        label = f"Final warning ({number} of {LIMIT})" if final else f"Warning {number} of {LIMIT}"
        await self._tell([who["user_id"]], label + ": " + t.name,
                         f"{t.name} on {day:%d %b %Y}. " + ("This is your final warning: the administration will now decide what action to take. "
                                                           if final else f"Warnings count for 12 months; a {LIMIT}rd one is final. ")
                         + "Open My warnings to read it and confirm you have read it. You can also reply.", MY_PAGE)
        if kind == "STUDENT":
            await self._text_student(person_id, f"{who['name']} ({who['code']}) has been given {'a FINAL warning' if final else f'warning {number} of {LIMIT}'} "
                                                f"for: {t.name}. Please contact the institute.")
        if final:
            for uid, link in [(u, PRINCIPAL_PAGE) for u in await self._deciders()]:
                await self._tell([uid], f"Final warning: decide the action for {who['name']}",
                                 f"{who['name']} ({who['code']}) has received a {LIMIT}rd warning within 12 months ({t.name}). Choose the action.", link)
        return w

    # ------------------------------------------------------------------ the person
    async def _mine(self, user_id) -> tuple[str, object] | None:
        from app.employees.models import Employee
        from app.students.models import Student

        s = (await self.session.execute(select(Student).where(Student.user_id == user_id))).scalar_one_or_none()
        if s is not None:
            return "STUDENT", s
        e = (await self.session.execute(select(Employee).where(Employee.user_id == user_id))).scalar_one_or_none()
        return ("EMPLOYEE", e) if e is not None else None

    async def mine(self, user_id) -> dict:
        me = await self._mine(user_id)
        if me is None:
            return {"warnings": [], "counting": 0, "limit": LIMIT, "count_days": COUNT_DAYS}
        kind, p = me
        col = DisciplinaryWarning.student_id if kind == "STUDENT" else DisciplinaryWarning.employee_id
        rows = (await self.session.execute(select(DisciplinaryWarning).where(col == p.id).order_by(DisciplinaryWarning.created_at.desc()))).scalars().all()
        return {"warnings": [self._out(w) for w in rows], "counting": await self._counting(kind, p.id), "limit": LIMIT, "count_days": COUNT_DAYS}

    async def acknowledge(self, warning_id: uuid.UUID, *, user_id) -> DisciplinaryWarning:
        """'I have read this': recorded once, with the time."""
        w = await self._get(warning_id, lock=True)
        me = await self._mine(user_id)
        if me is None or me[1].id != (w.student_id or w.employee_id):
            raise ResourceNotFoundError("Warning not found.")
        if w.acknowledged_at is None:
            w.acknowledged_at = datetime.now(timezone.utc)
            await self.audit.log(actor_user_id=user_id, action="warning.acknowledged", entity_type="disciplinary_warning", entity_id=w.id)
        return w

    async def respond(self, warning_id: uuid.UUID, *, user_id, text: str) -> DisciplinaryWarning:
        w = await self._get(warning_id, lock=True)
        me = await self._mine(user_id)
        if me is None or me[1].id != (w.student_id or w.employee_id):
            raise ResourceNotFoundError("Warning not found.")
        text = (text or "").strip()
        if len(text) < 10:
            raise ValidationAppError("Write your reply (at least 10 characters).")
        if w.status != "ACTIVE":
            raise BusinessRuleViolationError("This warning has been withdrawn.")
        if w.decided_at is not None:
            raise BusinessRuleViolationError("A decision has already been made on this warning.")
        if w.response is not None:
            raise BusinessRuleViolationError("You have already replied to this warning.")
        if w.acknowledged_at is None:
            w.acknowledged_at = datetime.now(timezone.utc)          # replying shows it was read
        w.response, w.responded_at = text[:4000], datetime.now(timezone.utc)
        await self.audit.log(actor_user_id=user_id, action="warning.replied", entity_type="disciplinary_warning", entity_id=w.id)
        who = self._describe(*me)
        await self._tell(await self._deciders(), f"Reply to a warning from {who['name']}",
                         f"{who['name']} ({who['code']}) replied to warning {reference(w)} ({w.violation}).", PRINCIPAL_PAGE)
        return w

    # ------------------------------------------------------------------ admin / principal
    async def _get(self, warning_id: uuid.UUID, *, lock: bool = False) -> DisciplinaryWarning:
        stmt = select(DisciplinaryWarning).where(DisciplinaryWarning.id == warning_id)
        w = (await self.session.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
        if w is None:
            raise ResourceNotFoundError("Warning not found.")
        return w

    async def withdraw(self, warning_id: uuid.UUID, *, actor_id, reason: str) -> DisciplinaryWarning:
        reason = (reason or "").strip()
        if len(reason) < 5:
            raise ValidationAppError("Say why it is withdrawn.")
        w = await self._get(warning_id, lock=True)
        if w.status != "ACTIVE":
            raise BusinessRuleViolationError("This warning is already withdrawn.")
        if w.decided_at is not None and w.action not in (None, "NONE"):
            raise BusinessRuleViolationError("An action was already taken on this warning. Change the person's status from their record if needed.")
        w.status, w.withdrawn_by, w.withdrawn_at, w.withdraw_reason = "WITHDRAWN", actor_id, datetime.now(timezone.utc), reason[:300]
        if w.action == "PENDING":
            w.action = None
        await self.audit.log(actor_user_id=actor_id, action="warning.withdrawn", entity_type="disciplinary_warning", entity_id=w.id, metadata={"reason": reason})
        kind = _kind(w)
        who = self._describe(kind, await self._person(kind, w.student_id or w.employee_id))
        await self._tell([who["user_id"]], "Warning withdrawn", f"Warning {reference(w)} ({w.violation}) has been withdrawn: {reason}. It no longer counts.", MY_PAGE)
        return w

    async def decide(self, warning_id: uuid.UUID, *, actor_id, action: str, note: str, amount: float | None = None) -> DisciplinaryWarning:
        action = (action or "").upper()
        note = (note or "").strip()
        w = await self._get(warning_id, lock=True)
        kind = _kind(w)
        if not w.is_final or w.action != "PENDING" or w.status != "ACTIVE":
            raise BusinessRuleViolationError("Only a final warning waiting for a decision can be decided.")
        if action not in ACTIONS[kind]:
            raise ValidationAppError("Choose one of the actions shown.")
        if len(note) < 10:
            raise ValidationAppError("Write the decision (at least 10 characters). The person will read it.")
        if action == "FINE" and not (amount and 0 < float(amount) <= MAX_FINE):
            raise ValidationAppError("Enter the fine in rupees.")
        p = await self._person(kind, w.student_id or w.employee_id)
        reason = f"Warning {reference(w)}: {note}"[:255]
        if kind == "STUDENT" and action in ("SUSPEND", "EXPEL"):
            from app.students.models import StudentStatus
            from app.students.service import StudentService

            await StudentService(self.session).change_status(p.id, new_status=StudentStatus.SUSPENDED if action == "SUSPEND" else StudentStatus.DROPPED,
                                                             reason=reason, actor_id=actor_id)
        elif kind == "EMPLOYEE" and action in ("SUSPEND", "TERMINATE"):
            from app.employees.models import EmployeeStatus
            from app.employees.service import EmployeeService

            if action == "SUSPEND":
                await self._block_sign_in(p, actor_id)                 # checked before anything changes
            await EmployeeService(self.session).change_status(p.id, new_status=EmployeeStatus.ON_LEAVE if action == "SUSPEND" else EmployeeStatus.TERMINATED,
                                                              reason=reason, actor_id=actor_id)
            if action == "SUSPEND":
                await self._block_sign_in(p, actor_id, apply=True)
        if action == "FINE":
            from app.fines.models import FinePayerType, FineSource
            from app.fines.service import FineService

            fine = await FineService(self.session).issue(
                payer_type=FinePayerType.STUDENT if kind == "STUDENT" else FinePayerType.EMPLOYEE, payer_id=p.id, amount=round(float(amount), 2),
                reason=f"Final warning {reference(w)} ({w.violation}): {note}"[:500], source=FineSource.DISCIPLINARY, actor_id=actor_id,
                related_entity_type="disciplinary_warning", related_entity_id=w.id, notify_title="A fine for your final warning")
            w.fine_id, w.fine_amount = fine.id, round(float(amount), 2)
        w.action, w.action_note, w.decided_by, w.decided_at = action, note[:500], actor_id, datetime.now(timezone.utc)
        await self.audit.log(actor_user_id=actor_id, action="warning.decided", entity_type="disciplinary_warning", entity_id=w.id,
                             after={"action": action, "amount": float(amount) if action == "FINE" else None}, metadata={"note": note})
        who = self._describe(kind, p)
        text = ACTION_TEXT[action] + (f" Rs. {float(amount):,.0f}" if action == "FINE" else "")
        await self._tell([who["user_id"]], f"Decision on your final warning: {text}", f"{note} Open My warnings for details.", MY_PAGE)
        if kind == "STUDENT":
            await self._text_student(p.id, f"Decision on the final warning of {who['name']} ({who['code']}): {text}. Please contact the institute.")
        return w

    async def _block_sign_in(self, employee, actor_id, *, apply: bool = False) -> None:
        """A suspended staff member cannot sign in (On leave alone does not block it). Never one's own, never the last admin."""
        if employee.user_id is None:
            return
        from app.auth.models import User
        from app.core.controls import ensure_another_admin, refuse_self

        if not apply:
            refuse_self(actor_id, employee.user_id, "You cannot suspend yourself.")
            await ensure_another_admin(self.session, employee.user_id)
            return
        user = await self.session.get(User, employee.user_id)
        if user is not None:
            user.is_active = False

    async def lift(self, warning_id: uuid.UUID, *, actor_id, note: str) -> DisciplinaryWarning:
        """End a suspension: the person is active again and can sign in. The warning stays on record."""
        note = (note or "").strip()
        if len(note) < 5:
            raise ValidationAppError("Say why the suspension ends.")
        w = await self._get(warning_id, lock=True)
        if w.action != "SUSPEND" or w.lifted_at is not None:
            raise BusinessRuleViolationError("Only a suspension still in force can be lifted.")
        kind = _kind(w)
        p = await self._person(kind, w.student_id or w.employee_id)
        reason = f"Suspension lifted ({reference(w)}): {note}"[:255]
        if kind == "STUDENT":
            from app.students.models import StudentStatus
            from app.students.service import StudentService

            if p.status == StudentStatus.SUSPENDED:
                await StudentService(self.session).change_status(p.id, new_status=StudentStatus.ACTIVE, reason=reason, actor_id=actor_id)
        else:
            from app.auth.models import User
            from app.employees.models import EmployeeStatus
            from app.employees.service import EmployeeService

            if p.status == EmployeeStatus.ON_LEAVE:
                await EmployeeService(self.session).change_status(p.id, new_status=EmployeeStatus.ACTIVE, reason=reason, actor_id=actor_id)
            if p.status == EmployeeStatus.ACTIVE and p.user_id:
                user = await self.session.get(User, p.user_id)
                if user is not None:
                    user.is_active = True
        w.lifted_at, w.lifted_by, w.lift_note = datetime.now(timezone.utc), actor_id, note[:300]
        await self.audit.log(actor_user_id=actor_id, action="warning.suspension_lifted", entity_type="disciplinary_warning", entity_id=w.id,
                             metadata={"note": note})
        who = self._describe(kind, p)
        await self._tell([who["user_id"]], "Your suspension has ended", f"{note} You can use the portal again.", MY_PAGE)
        return w

    def _out(self, w: DisciplinaryWarning, person: dict | None = None) -> dict:
        d = {"id": w.id, "reference": reference(w), "kind": _kind(w), "violation": w.violation, "note": w.note, "incident_date": w.incident_date,
             "issued_on": w.created_at, "number": w.number, "is_final": w.is_final, "status": w.status, "counts": counts_now(w),
             "counts_until": (w.created_at + timedelta(days=COUNT_DAYS)).date() if w.created_at else None,
             "response": w.response, "responded_at": w.responded_at, "withdrawn_at": w.withdrawn_at, "withdraw_reason": w.withdraw_reason,
             "action": w.action, "action_text": ACTION_TEXT.get(w.action or "", None), "action_note": w.action_note, "decided_at": w.decided_at,
             "acknowledged_at": w.acknowledged_at, "fine_id": w.fine_id, "fine_amount": float(w.fine_amount) if w.fine_amount is not None else None, "lifted_at": w.lifted_at, "lift_note": w.lift_note,
             "can_lift": w.action == "SUSPEND" and w.lifted_at is None,
             "can_reply": w.status == "ACTIVE" and w.decided_at is None and w.response is None}
        if person is not None:
            d["person"] = person
        return d

    async def office(self, *, view: str = "all", kind: str | None = None, person_id: uuid.UUID | None = None, limit: int = 300) -> dict:
        from app.employees.models import Employee
        from app.students.models import Student

        stmt = select(DisciplinaryWarning)
        if view == "pending":
            stmt = stmt.where(DisciplinaryWarning.action == "PENDING", DisciplinaryWarning.status == "ACTIVE")
        elif view == "replies":
            stmt = stmt.where(DisciplinaryWarning.response.is_not(None), DisciplinaryWarning.status == "ACTIVE", DisciplinaryWarning.decided_at.is_(None))
        if kind == "STUDENT":
            stmt = stmt.where(DisciplinaryWarning.student_id.is_not(None))
        elif kind == "EMPLOYEE":
            stmt = stmt.where(DisciplinaryWarning.employee_id.is_not(None))
        if person_id is not None:
            stmt = stmt.where(or_(DisciplinaryWarning.student_id == person_id, DisciplinaryWarning.employee_id == person_id))
        rows = (await self.session.execute(stmt.order_by(DisciplinaryWarning.created_at.desc()).limit(limit))).scalars().all()
        sids, eids = {w.student_id for w in rows if w.student_id}, {w.employee_id for w in rows if w.employee_id}
        people = {}
        if sids:
            people.update({s.id: self._describe("STUDENT", s) for s in (await self.session.execute(select(Student).where(Student.id.in_(sids)))).scalars()})
        if eids:
            people.update({e.id: self._describe("EMPLOYEE", e) for e in (await self.session.execute(select(Employee).where(Employee.id.in_(eids)))).scalars()})
        counting: dict = {}
        out = []
        for w in rows:
            pid = w.student_id or w.employee_id
            if pid not in counting:
                counting[pid] = await self._counting(_kind(w), pid)
            person = dict(people.get(pid, {}))
            person.pop("user_id", None)
            person["counting"] = counting[pid]
            out.append(self._out(w, person))
        pending = (await self.session.execute(select(func.count()).select_from(DisciplinaryWarning).where(
            DisciplinaryWarning.action == "PENDING", DisciplinaryWarning.status == "ACTIVE"))).scalar_one()
        replies = (await self.session.execute(select(func.count()).select_from(DisciplinaryWarning).where(
            DisciplinaryWarning.response.is_not(None), DisciplinaryWarning.status == "ACTIVE", DisciplinaryWarning.decided_at.is_(None)))).scalar_one()
        return {"warnings": out, "pending": pending, "replies": replies, "limit": LIMIT, "count_days": COUNT_DAYS, "actions": ACTIONS, "action_text": ACTION_TEXT}

    async def letter_parts(self, warning_id: uuid.UUID, *, user_id=None) -> dict:
        """What the letter needs. user_id given: it must be that person's own warning."""
        w = await self._get(warning_id)
        kind = _kind(w)
        p = await self._person(kind, w.student_id or w.employee_id)
        if user_id is not None and p.user_id != user_id:
            raise ResourceNotFoundError("Warning not found.")
        who = self._describe(kind, p)
        extra = ""
        if kind == "STUDENT" and p.program_id:
            from app.programs.models import Program

            prog = await self.session.get(Program, p.program_id)
            extra = prog.name if prog else ""
        elif kind == "EMPLOYEE":
            from app.employees.models import JobRole

            job = await self.session.get(JobRole, p.job_role_id)
            extra = job.title if job else ""
        return {"warning": self._out(w), "person": who, "detail": extra}
