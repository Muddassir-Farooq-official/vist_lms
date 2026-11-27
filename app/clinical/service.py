"""Clinical rotations.

  * The administration sets up hospitals (sites) and their wards (units, with how many students each takes), clinical
    groups of students, supervisors (an employee, or a hospital staff member given a limited "Clinical supervisor"
    login), the logbook skills each program requires, and rotations: a group at a ward for a date range, on chosen
    weekdays and hours, with a supervisor. A rotation is refused when the group is already somewhere else on those
    dates, or the ward would hold more students than it takes.
  * The supervisor marks each day's clinical attendance (kept apart from class attendance) and signs or rejects the
    students' logbook entries. A supervisor only ever sees their own rotations.
  * Students log what they did (skill, day, how many times) during a rotation and follow their progress against what
    the program requires. A placement letter (PDF) goes to the hospital for each rotation.
"""
from __future__ import annotations

import io
import secrets
import string
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.models import (ClinicalAttendance, ClinicalGroup, ClinicalGroupMember, ClinicalRotation, ClinicalSite, ClinicalSkill,
                                 ClinicalSupervisor, ClinicalUnit, LogEntry)
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError, ValidationAppError

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _days(text: str) -> list[int]:
    return sorted({int(x) for x in (text or "").split(",") if x.strip().isdigit() and 0 <= int(x) <= 6})


def _overlap(a1: date, a2: date, b1: date, b2: date) -> bool:
    return a1 <= b2 and b1 <= a2


class ClinicalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ setup lists
    async def setup(self) -> dict:
        from app.programs.models import Program
        from app.students.models import Student

        sites = (await self.session.execute(select(ClinicalSite).order_by(ClinicalSite.name))).scalars().all()
        units = (await self.session.execute(select(ClinicalUnit).order_by(ClinicalUnit.name))).scalars().all()
        groups = (await self.session.execute(select(ClinicalGroup).order_by(ClinicalGroup.name))).scalars().all()
        sizes = dict((await self.session.execute(select(ClinicalGroupMember.group_id, func.count()).group_by(ClinicalGroupMember.group_id))).all())
        sups = (await self.session.execute(select(ClinicalSupervisor).order_by(ClinicalSupervisor.name))).scalars().all()
        programs = {p.id: p.code for p in (await self.session.execute(select(Program))).scalars()}
        skills = (await self.session.execute(select(ClinicalSkill).order_by(ClinicalSkill.name))).scalars().all()
        _ = Student
        by_site = defaultdict(list)
        for u in units:
            by_site[u.site_id].append({"id": u.id, "name": u.name, "capacity": u.capacity})
        return {"sites": [{"id": s.id, "name": s.name, "address": s.address, "contact_person": s.contact_person, "contact_phone": s.contact_phone,
                           "is_active": s.is_active, "units": by_site[s.id]} for s in sites],
                "groups": [{"id": g.id, "name": g.name, "program_id": g.program_id, "program": programs.get(g.program_id), "semester": g.semester,
                            "students": int(sizes.get(g.id, 0))} for g in groups],
                "supervisors": [{"id": x.id, "name": x.name, "phone": x.phone, "site_id": x.site_id, "internal": x.employee_id is not None, "is_active": x.is_active}
                                for x in sups],
                "skills": [{"id": k.id, "program_id": k.program_id, "program": programs.get(k.program_id), "name": k.name, "required": k.required,
                            "semester": k.semester, "is_active": k.is_active} for k in skills],
                "programs": [{"id": pid, "code": code} for pid, code in sorted(programs.items(), key=lambda kv: kv[1])]}

    async def save_site(self, data: dict, *, site_id: uuid.UUID | None = None) -> dict:
        name = " ".join((data.get("name") or "").split())
        if len(name) < 2:
            raise ValidationAppError("Give the hospital a name.")
        clash = (await self.session.execute(select(ClinicalSite).where(func.lower(ClinicalSite.name) == name.lower()))).scalar_one_or_none()
        if clash and clash.id != site_id:
            raise DuplicateResourceError("A hospital with that name exists.")
        s = await self.session.get(ClinicalSite, site_id) if site_id else None
        if site_id and s is None:
            raise ResourceNotFoundError("Hospital not found.")
        if s is None:
            s = ClinicalSite(name=name)
            self.session.add(s)
        s.name, s.address, s.contact_person, s.contact_phone = name, (data.get("address") or None), (data.get("contact_person") or None), (data.get("contact_phone") or None)
        if "is_active" in data:
            s.is_active = bool(data["is_active"])
        await self.session.flush()
        return {"id": s.id}

    async def save_unit(self, site_id: uuid.UUID, *, name: str, capacity: int, unit_id: uuid.UUID | None = None) -> dict:
        if await self.session.get(ClinicalSite, site_id) is None:
            raise ResourceNotFoundError("Hospital not found.")
        name = " ".join((name or "").split())
        if len(name) < 2 or not (1 <= capacity <= 200):
            raise ValidationAppError("Give the ward a name and how many students it takes (1-200).")
        u = await self.session.get(ClinicalUnit, unit_id) if unit_id else None
        if u is None:
            u = ClinicalUnit(site_id=site_id, name=name, capacity=capacity)
            self.session.add(u)
        else:
            u.name, u.capacity = name, capacity
        await self.session.flush()
        return {"id": u.id}

    async def save_group(self, *, name: str, program_id: uuid.UUID | None, semester: int | None, student_ids: list[uuid.UUID],
                         group_id: uuid.UUID | None = None) -> dict:
        from app.students.models import Student

        name = " ".join((name or "").split())
        if len(name) < 2:
            raise ValidationAppError("Give the group a name.")
        g = await self.session.get(ClinicalGroup, group_id) if group_id else None
        if group_id and g is None:
            raise ResourceNotFoundError("Group not found.")
        clash = (await self.session.execute(select(ClinicalGroup).where(func.lower(ClinicalGroup.name) == name.lower()))).scalar_one_or_none()
        if clash and (g is None or clash.id != g.id):
            raise DuplicateResourceError("A group with that name exists.")
        found = set((await self.session.execute(select(Student.id).where(Student.id.in_(student_ids or [uuid.uuid4()])))).scalars())
        if set(student_ids) - found:
            raise ValidationAppError("A chosen student was not found.")
        if g is None:
            g = ClinicalGroup(name=name)
            self.session.add(g)
            await self.session.flush()
        g.name, g.program_id, g.semester = name, program_id, semester
        await self.session.execute(delete(ClinicalGroupMember).where(ClinicalGroupMember.group_id == g.id))
        for sid in dict.fromkeys(student_ids):
            self.session.add(ClinicalGroupMember(group_id=g.id, student_id=sid))
        await self.session.flush()
        return {"id": g.id, "students": len(set(student_ids))}

    async def group_students(self, group_id: uuid.UUID) -> list[dict]:
        from app.students.models import Student

        rows = (await self.session.execute(select(Student).join(ClinicalGroupMember, ClinicalGroupMember.student_id == Student.id)
                                           .where(ClinicalGroupMember.group_id == group_id).order_by(Student.roll_number))).scalars().all()
        return [{"id": s.id, "roll_number": s.roll_number, "name": f"{s.first_name} {s.last_name}"} for s in rows]

    async def save_skill(self, *, program_id: uuid.UUID, name: str, required: int, semester: int | None, skill_id: uuid.UUID | None = None,
                         is_active: bool = True) -> dict:
        name = " ".join((name or "").split())
        if len(name) < 2 or not (1 <= required <= 500):
            raise ValidationAppError("Give the skill a name and how many times it is required (1-500).")
        k = await self.session.get(ClinicalSkill, skill_id) if skill_id else None
        if k is None:
            if (await self.session.execute(select(ClinicalSkill).where(ClinicalSkill.program_id == program_id, func.lower(ClinicalSkill.name) == name.lower()))).first():
                raise DuplicateResourceError("This program already has that skill.")
            k = ClinicalSkill(program_id=program_id, name=name, required=required, semester=semester)
            self.session.add(k)
        else:
            k.name, k.required, k.semester, k.is_active = name, required, semester, is_active
        await self.session.flush()
        return {"id": k.id}

    # ------------------------------------------------------------------ supervisors
    async def add_supervisor(self, *, name: str, phone: str | None, site_id: uuid.UUID | None, employee_id: uuid.UUID | None, username: str | None,
                             actor_id: uuid.UUID) -> dict:
        """An employee supervises with their own login; a hospital's staff member gets a new limited login."""
        from app.audit.service import AuditService
        from app.auth.models import Role, User, UserRole
        from app.auth.permissions import Roles
        from app.core.security import hash_password
        from app.employees.models import Employee

        temp = None
        if employee_id:
            e = await self.session.get(Employee, employee_id)
            if e is None or e.user_id is None:
                raise ValidationAppError("That employee has no login.")
            user_id, name = e.user_id, f"{e.first_name} {e.last_name}"
        else:
            name = " ".join((name or "").split())
            username = (username or "").strip().lower()
            if len(name) < 3 or len(username) < 4 or not all(c.isalnum() or c in "._-" for c in username):
                raise ValidationAppError("Give the supervisor's name and a username (at least 4 letters or digits).")
            if (await self.session.execute(select(User.id).where(func.lower(User.username) == username))).first():
                raise DuplicateResourceError("That username is taken.")
            role = (await self.session.execute(select(Role).where(Role.code == Roles.CLINICAL_SUPERVISOR))).scalar_one_or_none()
            if role is None:
                raise BusinessRuleViolationError("The Clinical supervisor role does not exist yet. Run the permission seed script.")
            temp = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10)) + "#7"
            user = User(username=username, password_hash=hash_password(temp), is_active=True, must_change_password=True)
            self.session.add(user)
            await self.session.flush()
            self.session.add(UserRole(user_id=user.id, role_id=role.id))
            user_id = user.id
        if (await self.session.execute(select(ClinicalSupervisor).where(ClinicalSupervisor.user_id == user_id))).first():
            raise DuplicateResourceError("This person is already a supervisor.")
        sup = ClinicalSupervisor(user_id=user_id, name=name, phone=(phone or None), site_id=site_id, employee_id=employee_id)
        self.session.add(sup)
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="clinical.supervisor_added", entity_type="clinical_supervisor", entity_id=sup.id,
                                             after={"name": name, "internal": bool(employee_id)})
        return {"id": sup.id, "username": username if not employee_id else None, "temporary_password": temp}

    async def set_supervisor_active(self, supervisor_id: uuid.UUID, active: bool) -> dict:
        from app.auth.models import User

        sup = await self.session.get(ClinicalSupervisor, supervisor_id)
        if sup is None:
            raise ResourceNotFoundError("Supervisor not found.")
        sup.is_active = active
        if sup.employee_id is None:                       # a hospital login: switched on and off with it
            u = await self.session.get(User, sup.user_id)
            if u:
                u.is_active = active
        await self.session.flush()
        return {"id": sup.id, "is_active": active}

    # ------------------------------------------------------------------ rotations
    async def _check_rotation(self, *, group_id, unit_id, start_date, end_date, exclude: uuid.UUID | None) -> None:
        if end_date < start_date:
            raise ValidationAppError("The rotation must end on or after its first day.")
        if (end_date - start_date).days > 366:
            raise ValidationAppError("A rotation can be at most a year.")
        unit = await self.session.get(ClinicalUnit, unit_id)
        if unit is None or await self.session.get(ClinicalGroup, group_id) is None:
            raise ResourceNotFoundError("Ward or group not found.")
        others = (await self.session.execute(select(ClinicalRotation).where(ClinicalRotation.start_date <= end_date, ClinicalRotation.end_date >= start_date,
                                                                            ClinicalRotation.id != (exclude or uuid.uuid4())))).scalars().all()
        members = set((await self.session.execute(select(ClinicalGroupMember.student_id).where(ClinicalGroupMember.group_id == group_id))).scalars())
        for r in others:
            u = await self.session.get(ClinicalUnit, r.unit_id)
            if r.group_id == group_id:
                raise BusinessRuleViolationError(f"This group is already at {u.name} from {r.start_date:%d %b} to {r.end_date:%d %b}.")
            both = members & set((await self.session.execute(select(ClinicalGroupMember.student_id).where(ClinicalGroupMember.group_id == r.group_id))).scalars())
            if both:
                g = await self.session.get(ClinicalGroup, r.group_id)
                raise BusinessRuleViolationError(f"{len(both)} student(s) of this group are already at {u.name} with {g.name} "
                                                 f"from {r.start_date:%d %b} to {r.end_date:%d %b}.")
        size = int((await self.session.execute(select(func.count()).select_from(ClinicalGroupMember).where(ClinicalGroupMember.group_id == group_id))).scalar_one())
        sizes = dict((await self.session.execute(select(ClinicalGroupMember.group_id, func.count()).group_by(ClinicalGroupMember.group_id))).all())
        # the busiest day in the range: overlapping rotations at the same ward
        same = [r for r in others if r.unit_id == unit_id]
        day = start_date
        while day <= end_date and same:
            load = size + sum(int(sizes.get(r.group_id, 0)) for r in same if r.start_date <= day <= r.end_date)
            if load > unit.capacity:
                raise BusinessRuleViolationError(f"{unit.name} takes {unit.capacity} students; on {day:%d %b} it would have {load}.")
            day += timedelta(days=1)
        if size > unit.capacity:
            raise BusinessRuleViolationError(f"{unit.name} takes {unit.capacity} students; this group has {size}.")

    async def save_rotation(self, data: dict, *, actor_id: uuid.UUID, rotation_id: uuid.UUID | None = None) -> dict:
        from app.audit.service import AuditService

        days = _days(data.get("days") or "")
        if not days:
            raise ValidationAppError("Choose the weekdays.")
        st, en = data["start_time"], data["end_time"]
        if en <= st:
            raise ValidationAppError("The day must end after it starts.")
        if data.get("supervisor_id") and await self.session.get(ClinicalSupervisor, data["supervisor_id"]) is None:
            raise ResourceNotFoundError("Supervisor not found.")
        await self._check_rotation(group_id=data["group_id"], unit_id=data["unit_id"], start_date=data["start_date"], end_date=data["end_date"], exclude=rotation_id)
        r = await self.session.get(ClinicalRotation, rotation_id) if rotation_id else None
        if rotation_id and r is None:
            raise ResourceNotFoundError("Rotation not found.")
        if r is None:
            r = ClinicalRotation(group_id=data["group_id"], unit_id=data["unit_id"], start_date=data["start_date"], end_date=data["end_date"],
                                 start_time=st, end_time=en, created_by=actor_id)
            self.session.add(r)
        r.group_id, r.unit_id, r.supervisor_id = data["group_id"], data["unit_id"], data.get("supervisor_id")
        r.start_date, r.end_date, r.start_time, r.end_time = data["start_date"], data["end_date"], st, en
        r.days, r.note = ",".join(str(d) for d in days), (data.get("note") or None)
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="clinical.rotation_saved", entity_type="clinical_rotation", entity_id=r.id,
                                             after={"group": str(r.group_id), "unit": str(r.unit_id), "from": str(r.start_date), "to": str(r.end_date)})
        return {"id": r.id}

    async def delete_rotation(self, rotation_id: uuid.UUID) -> None:
        r = await self.session.get(ClinicalRotation, rotation_id)
        if r is None:
            raise ResourceNotFoundError("Rotation not found.")
        used = (await self.session.execute(select(func.count()).select_from(ClinicalAttendance).where(ClinicalAttendance.rotation_id == rotation_id))).scalar_one() + \
            (await self.session.execute(select(func.count()).select_from(LogEntry).where(LogEntry.rotation_id == rotation_id))).scalar_one()
        if used:
            raise BusinessRuleViolationError("This rotation already has attendance or logbook entries; change its dates instead.")
        await self.session.delete(r)
        await self.session.flush()

    async def _rot_rows(self, rotations) -> list[dict]:
        units = {u.id: u for u in (await self.session.execute(select(ClinicalUnit))).scalars()}
        sites = {s.id: s for s in (await self.session.execute(select(ClinicalSite))).scalars()}
        groups = {g.id: g for g in (await self.session.execute(select(ClinicalGroup))).scalars()}
        sups = {x.id: x for x in (await self.session.execute(select(ClinicalSupervisor))).scalars()}
        sizes = dict((await self.session.execute(select(ClinicalGroupMember.group_id, func.count()).group_by(ClinicalGroupMember.group_id))).all())
        out = []
        for r in rotations:
            u = units.get(r.unit_id)
            s = sites.get(u.site_id) if u else None
            out.append({"id": r.id, "group_id": r.group_id, "group": groups[r.group_id].name if r.group_id in groups else "?", "students": int(sizes.get(r.group_id, 0)),
                        "unit_id": r.unit_id, "unit": u.name if u else "?", "site": s.name if s else "?", "site_id": s.id if s else None,
                        "supervisor_id": r.supervisor_id, "supervisor": sups[r.supervisor_id].name if r.supervisor_id in sups else None,
                        "start_date": r.start_date, "end_date": r.end_date, "days": _days(r.days), "days_text": ", ".join(DAY_NAMES[d] for d in _days(r.days)),
                        "start_time": r.start_time.strftime("%H:%M"), "end_time": r.end_time.strftime("%H:%M"), "note": r.note})
        return out

    async def rotations(self, *, group_id: uuid.UUID | None = None, current_only: bool = False) -> list[dict]:
        from app.core.clock import institute_today

        q = select(ClinicalRotation).order_by(ClinicalRotation.start_date, ClinicalRotation.group_id)
        if group_id:
            q = q.where(ClinicalRotation.group_id == group_id)
        if current_only:
            q = q.where(ClinicalRotation.end_date >= institute_today())
        return await self._rot_rows((await self.session.execute(q)).scalars().all())

    # ------------------------------------------------------------------ the supervisor
    async def supervisor_of(self, user) -> ClinicalSupervisor | None:
        return (await self.session.execute(select(ClinicalSupervisor).where(ClinicalSupervisor.user_id == user.id, ClinicalSupervisor.is_active.is_(True)))).scalar_one_or_none()

    async def _own_rotation(self, rotation_id: uuid.UUID, user, *, manager: bool) -> ClinicalRotation:
        r = await self.session.get(ClinicalRotation, rotation_id)
        if r is None:
            raise ResourceNotFoundError("Rotation not found.")
        if manager:
            return r
        sup = await self.supervisor_of(user)
        if sup is None or r.supervisor_id != sup.id:
            raise AuthorizationError("This rotation is supervised by someone else.")
        return r

    async def my_rotations(self, user) -> list[dict]:
        sup = await self.supervisor_of(user)
        if sup is None:
            return []
        from app.core.clock import institute_today

        rows = (await self.session.execute(select(ClinicalRotation).where(ClinicalRotation.supervisor_id == sup.id,
                                                                          ClinicalRotation.end_date >= institute_today() - timedelta(days=30))
                                           .order_by(ClinicalRotation.start_date))).scalars().all()
        out = await self._rot_rows(rows)
        waiting = dict((await self.session.execute(select(LogEntry.rotation_id, func.count()).where(LogEntry.rotation_id.in_([r.id for r in rows] or [uuid.uuid4()]),
                                                                                                  LogEntry.status == "SUBMITTED").group_by(LogEntry.rotation_id))).all())
        for x in out:
            x["to_sign"] = int(waiting.get(x["id"], 0))
        return out

    async def day_sheet(self, rotation_id: uuid.UUID, day: date, user, *, manager: bool) -> dict:
        r = await self._own_rotation(rotation_id, user, manager=manager)
        marks = {a.student_id: a.status for a in (await self.session.execute(select(ClinicalAttendance).where(ClinicalAttendance.rotation_id == r.id,
                                                                                                             ClinicalAttendance.day == day))).scalars()}
        from app.students.models import StudentStatus
        from app.students.standing import locked_ids

        studs = await self.group_students(r.group_id)
        locked = await locked_ids(self.session, [s["id"] for s in studs])
        # expelled / left: off the sheet (unless already marked that day); suspended: on it, locked
        studs = [s for s in studs if locked.get(s["id"]) != StudentStatus.DROPPED or s["id"] in marks]
        for s in studs:
            s["status"] = marks.get(s["id"])
            s["suspended"] = locked.get(s["id"]) == StudentStatus.SUSPENDED
        return {"rotation_id": r.id, "day": day, "is_rotation_day": r.start_date <= day <= r.end_date and day.weekday() in _days(r.days), "students": studs}

    async def mark(self, rotation_id: uuid.UUID, day: date, entries: list[dict], user, *, manager: bool) -> dict:
        from app.core.clock import institute_today

        r = await self._own_rotation(rotation_id, user, manager=manager)
        if not (r.start_date <= day <= r.end_date) or day.weekday() not in _days(r.days):
            raise BusinessRuleViolationError("That day is not a day of this rotation.")
        if day > institute_today():
            raise BusinessRuleViolationError("Attendance cannot be marked for a day that has not come.")
        if not manager and (institute_today() - day).days > 7:
            raise BusinessRuleViolationError("Attendance older than a week is corrected by the administration.")
        from app.students.standing import locked_ids

        members = {s["id"] for s in await self.group_students(r.group_id)}
        locked = await locked_ids(self.session, members)
        n = 0
        for x in entries:
            sid, status = uuid.UUID(str(x["student_id"])), str(x["status"]).upper()
            if sid not in members:
                raise ValidationAppError("A student is not in this group.")
            if sid in locked:
                continue            # suspended or expelled: not marked (their attendance is locked)
            if status not in ("PRESENT", "ABSENT", "LEAVE"):
                raise ValidationAppError("Present, absent or leave.")
            row = (await self.session.execute(select(ClinicalAttendance).where(ClinicalAttendance.rotation_id == r.id, ClinicalAttendance.student_id == sid,
                                                                               ClinicalAttendance.day == day))).scalar_one_or_none()
            if row is None:
                self.session.add(ClinicalAttendance(rotation_id=r.id, student_id=sid, day=day, status=status, marked_by=user.id))
            else:
                row.status, row.marked_by, row.marked_at = status, user.id, datetime.now(timezone.utc)
            n += 1
        await self.session.flush()
        return {"marked": n}

    async def to_sign(self, rotation_id: uuid.UUID, user, *, manager: bool) -> list[dict]:
        from app.students.models import Student

        r = await self._own_rotation(rotation_id, user, manager=manager)
        rows = (await self.session.execute(select(LogEntry, ClinicalSkill, Student).join(ClinicalSkill, ClinicalSkill.id == LogEntry.skill_id)
                                           .join(Student, Student.id == LogEntry.student_id).where(LogEntry.rotation_id == r.id)
                                           .order_by(LogEntry.status != "SUBMITTED", LogEntry.day.desc()))).all()
        return [{"id": e.id, "student": f"{s.first_name} {s.last_name}", "roll_number": s.roll_number, "skill": k.name, "day": e.day, "times": e.times,
                 "note": e.note, "status": e.status, "reject_note": e.reject_note} for e, k, s in rows]

    async def sign(self, entry_id: uuid.UUID, *, approve: bool, note: str | None, user, manager: bool) -> dict:
        e = await self.session.get(LogEntry, entry_id)
        if e is None:
            raise ResourceNotFoundError("Entry not found.")
        await self._own_rotation(e.rotation_id, user, manager=manager)
        if e.status != "SUBMITTED":
            raise BusinessRuleViolationError("This entry was already decided.")
        if not approve and len((note or "").strip()) < 3:
            raise ValidationAppError("Say why it is not signed (the student sees this).")
        e.status = "SIGNED" if approve else "REJECTED"
        e.decided_by, e.decided_at, e.reject_note = user.id, datetime.now(timezone.utc), None if approve else note.strip()[:300]
        await self.session.flush()
        return {"status": e.status}

    # ------------------------------------------------------------------ the student
    async def student_view(self, student) -> dict:
        from app.core.clock import institute_today

        groups = [g for g, in (await self.session.execute(select(ClinicalGroupMember.group_id).where(ClinicalGroupMember.student_id == student.id))).all()]
        rows = (await self.session.execute(select(ClinicalRotation).where(ClinicalRotation.group_id.in_(groups or [uuid.uuid4()])).order_by(ClinicalRotation.start_date))).scalars().all()
        rots = await self._rot_rows(rows)
        today = institute_today()
        att = defaultdict(lambda: {"PRESENT": 0, "ABSENT": 0, "LEAVE": 0})
        for rid, status, n in (await self.session.execute(select(ClinicalAttendance.rotation_id, ClinicalAttendance.status, func.count()).where(
                ClinicalAttendance.student_id == student.id).group_by(ClinicalAttendance.rotation_id, ClinicalAttendance.status))).all():
            att[rid][status] = int(n)
        for r in rots:
            a = att[r["id"]]
            counted = a["PRESENT"] + a["ABSENT"]
            r["attendance"] = {**a, "percentage": round(100 * a["PRESENT"] / counted, 1) if counted else None}
            r["now"] = r["start_date"] <= today <= r["end_date"]
        return {"rotations": rots, "progress": await self.progress(student), "entries": await self._entries(student.id)}

    async def _entries(self, student_id: uuid.UUID) -> list[dict]:
        rows = (await self.session.execute(select(LogEntry, ClinicalSkill).join(ClinicalSkill, ClinicalSkill.id == LogEntry.skill_id)
                                           .where(LogEntry.student_id == student_id).order_by(LogEntry.day.desc()).limit(300))).all()
        return [{"id": e.id, "skill": k.name, "day": e.day, "times": e.times, "note": e.note, "status": e.status, "reject_note": e.reject_note} for e, k in rows]

    async def progress(self, student) -> list[dict]:
        if student.program_id is None:
            return []
        skills = (await self.session.execute(select(ClinicalSkill).where(ClinicalSkill.program_id == student.program_id, ClinicalSkill.is_active.is_(True))
                                             .order_by(ClinicalSkill.semester.nulls_first(), ClinicalSkill.name))).scalars().all()
        done = defaultdict(lambda: {"SIGNED": 0, "SUBMITTED": 0})
        for kid, status, n in (await self.session.execute(select(LogEntry.skill_id, LogEntry.status, func.sum(LogEntry.times)).where(
                LogEntry.student_id == student.id, LogEntry.status != "REJECTED").group_by(LogEntry.skill_id, LogEntry.status))).all():
            done[kid][status] = int(n or 0)
        return [{"skill_id": k.id, "skill": k.name, "semester": k.semester, "required": k.required, "signed": done[k.id]["SIGNED"],
                 "waiting": done[k.id]["SUBMITTED"], "complete": done[k.id]["SIGNED"] >= k.required} for k in skills]

    async def log(self, student, *, skill_id: uuid.UUID, rotation_id: uuid.UUID, day: date, times: int, note: str | None) -> dict:
        from app.core.clock import institute_today

        k = await self.session.get(ClinicalSkill, skill_id)
        if k is None or k.program_id != student.program_id or not k.is_active:
            raise ValidationAppError("Choose one of your program's skills.")
        r = await self.session.get(ClinicalRotation, rotation_id)
        mine = r is not None and await self.session.get(ClinicalGroupMember, (r.group_id, student.id)) is not None
        if not mine:
            raise ValidationAppError("Choose one of your rotations.")
        if not (r.start_date <= day <= r.end_date) or day > institute_today():
            raise ValidationAppError("The day must be within the rotation and not in the future.")
        if not (1 <= times <= 50):
            raise ValidationAppError("Times must be 1 to 50.")
        e = LogEntry(student_id=student.id, skill_id=k.id, rotation_id=r.id, day=day, times=times, note=(note or "").strip()[:500] or None)
        self.session.add(e)
        await self.session.flush()
        return {"id": e.id, "status": e.status}

    async def withdraw(self, student, entry_id: uuid.UUID) -> None:
        e = await self.session.get(LogEntry, entry_id)
        if e is None or e.student_id != student.id:
            raise ResourceNotFoundError("Entry not found.")
        if e.status != "SUBMITTED":
            raise BusinessRuleViolationError("A signed or rejected entry stays on record.")
        await self.session.delete(e)
        await self.session.flush()

    # ------------------------------------------------------------------ reports
    async def group_report(self, group_id: uuid.UUID) -> dict:
        from app.students.models import Student

        g = await self.session.get(ClinicalGroup, group_id)
        if g is None:
            raise ResourceNotFoundError("Group not found.")
        studs = (await self.session.execute(select(Student).join(ClinicalGroupMember, ClinicalGroupMember.student_id == Student.id)
                                            .where(ClinicalGroupMember.group_id == group_id).order_by(Student.roll_number))).scalars().all()
        rots = [r.id for r in (await self.session.execute(select(ClinicalRotation).where(ClinicalRotation.group_id == group_id))).scalars()]
        att = defaultdict(lambda: {"PRESENT": 0, "ABSENT": 0})
        for sid, status, n in (await self.session.execute(select(ClinicalAttendance.student_id, ClinicalAttendance.status, func.count()).where(
                ClinicalAttendance.rotation_id.in_(rots or [uuid.uuid4()])).group_by(ClinicalAttendance.student_id, ClinicalAttendance.status))).all():
            if status in ("PRESENT", "ABSENT"):
                att[sid][status] = int(n)
        out = []
        for s in studs:
            a = att[s.id]
            counted = a["PRESENT"] + a["ABSENT"]
            prog = await self.progress(s)
            out.append({"id": s.id, "roll_number": s.roll_number, "name": f"{s.first_name} {s.last_name}",
                        "attendance": round(100 * a["PRESENT"] / counted, 1) if counted else None, "days": counted,
                        "skills_complete": sum(1 for p in prog if p["complete"]), "skills": len(prog)})
        return {"group": g.name, "students": out}


# ====================================================================== placement letter
def placement_letter_pdf(rot: dict, site: dict, students: list[dict], issued: date) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    from app.core import brand

    buf = io.BytesIO()
    width = A4[0] - 40 * mm
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=16 * mm, bottomMargin=16 * mm, title="Placement letter", author=brand.name())
    base = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=base["Normal"], fontName="Helvetica", fontSize=10.5, leading=15)
    story = brand.platypus_header(width) + [Spacer(1, 6), Paragraph(f"Date: {issued:%d %B %Y}", body), Spacer(1, 8)]
    to = escape(site.get("contact_person") or "The Medical Superintendent")
    story += [Paragraph(f"To: {to}<br/>{escape(site['name'])}" + (f"<br/>{escape(site['address'])}" if site.get("address") else ""), body), Spacer(1, 10),
              Paragraph(f"<b>Subject: Clinical placement of students — {escape(rot['unit'])}</b>", body), Spacer(1, 8),
              Paragraph(f"We request you to allow the following {len(students)} student(s) of {escape(rot['group'])} to undertake their clinical training at "
                        f"<b>{escape(rot['unit'])}</b>, {escape(site['name'])}, from <b>{rot['start_date']:%d %b %Y}</b> to <b>{rot['end_date']:%d %b %Y}</b> "
                        f"({escape(rot['days_text'])}, {rot['start_time']}–{rot['end_time']})"
                        + (f", under the supervision of <b>{escape(rot['supervisor'])}</b>" if rot.get("supervisor") else "") + ".", body), Spacer(1, 10)]
    t = Table([["#", "Roll no.", "Name"]] + [[str(i + 1), s["roll_number"] or "", s["name"]] for i, s in enumerate(students)], colWidths=[12 * mm, 40 * mm, width - 52 * mm])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9.5), ("FONT", (0, 1), (-1, -1), "Helvetica", 9.5),
                           ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8d0e0")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f9"))]))
    story += [t, Spacer(1, 12), Paragraph("The students will follow the hospital's rules and dress code. Their attendance and logbooks are kept by the supervisor. "
                                          "We are grateful for your cooperation.", body), Spacer(1, 30), Paragraph("Principal / Clinical coordinator", body)]
    doc.build(story)
    return buf.getvalue()
