"""Student mentoring (models.py)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.clock import institute_today
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.mentoring.models import MentorAssignment, MentorMeeting

KINDS = {"MEETING": "Meeting", "PHONE": "Phone call", "PARENTS": "With parents"}
SIGN_TEXT = {"attendance": "attendance {v}%", "marks": "marks so far {v}%", "assignments": "{v} assignments missed"}


class MentoringService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def employee_of(self, user) -> uuid.UUID | None:
        from app.employees.models import Employee

        return (await self.session.execute(select(Employee.id).where(Employee.user_id == user.id))).scalar_one_or_none()

    async def _current(self, student_id: uuid.UUID) -> MentorAssignment | None:
        return (await self.session.execute(select(MentorAssignment).where(MentorAssignment.student_id == student_id,
                                                                          MentorAssignment.end_date.is_(None)))).scalar_one_or_none()

    # ------------------------------------------------------------------ the administration: who mentors whom
    async def assign(self, *, student_ids: list[uuid.UUID], mentor_ids: list[uuid.UUID], actor_id: uuid.UUID, only_unassigned: bool) -> dict:
        """Give each student a mentor; with several mentors the students are shared out evenly (fewest mentees first).
        A student who already has a mentor moves to the new one (the old assignment is closed, its notes stay)."""
        from app.employees.models import Employee
        from app.students.models import Student, StudentStatus

        if not student_ids or not mentor_ids:
            raise ValidationAppError("Choose the students and at least one mentor.")
        mentors = (await self.session.execute(select(Employee).where(Employee.id.in_(mentor_ids)))).scalars().all()
        if len(mentors) != len(set(mentor_ids)):
            raise ValidationAppError("A chosen mentor was not found.")
        if any(m.user_id is None for m in mentors):
            raise ValidationAppError("Every mentor needs a login to see their mentees.")
        load = dict((await self.session.execute(select(MentorAssignment.mentor_id, func.count()).where(MentorAssignment.end_date.is_(None),
                                                                                                         MentorAssignment.mentor_id.in_(mentor_ids))
                                                 .group_by(MentorAssignment.mentor_id))).all())
        load = {m.id: int(load.get(m.id, 0)) for m in mentors}
        students = (await self.session.execute(select(Student).where(Student.id.in_(student_ids)).order_by(Student.roll_number))).scalars().all()
        today = institute_today()
        done, kept = 0, 0
        for s in students:
            if s.status != StudentStatus.ACTIVE:
                continue
            cur = await self._current(s.id)
            if cur is not None and only_unassigned:
                kept += 1
                continue
            mentor = min(load, key=lambda k: (load[k], str(k)))
            if cur is not None and cur.mentor_id == mentor:
                kept += 1
                continue
            if cur is not None:
                cur.end_date = today
                await self.session.flush()
            self.session.add(MentorAssignment(student_id=s.id, mentor_id=mentor, start_date=today, assigned_by=actor_id))
            load[mentor] += 1
            done += 1
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="mentoring.assigned", entity_type="mentoring", entity_id=None,
                                             metadata={"students": done, "kept": kept, "mentors": [str(m) for m in mentor_ids]})
        return {"assigned": done, "unchanged": kept}

    async def unassign(self, student_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        cur = await self._current(student_id)
        if cur is None:
            raise BusinessRuleViolationError("This student has no mentor.")
        cur.end_date = institute_today()
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="mentoring.unassigned", entity_type="student", entity_id=student_id)

    async def overview(self, *, program_id: uuid.UUID | None = None, semester: int | None = None, unassigned_only: bool = False) -> dict:
        """Every current student with their mentor (administration)."""
        from app.employees.models import Employee
        from app.programs.models import Program
        from app.students.models import Student, StudentStatus

        q = (select(Student, MentorAssignment, Employee, Program.code).outerjoin(MentorAssignment, (MentorAssignment.student_id == Student.id) &
                                                                                 (MentorAssignment.end_date.is_(None)))
             .outerjoin(Employee, Employee.id == MentorAssignment.mentor_id).outerjoin(Program, Program.id == Student.program_id)
             .where(Student.status == StudentStatus.ACTIVE).order_by(Program.code, Student.current_semester, Student.roll_number))
        if program_id:
            q = q.where(Student.program_id == program_id)
        if semester:
            q = q.where(Student.current_semester == semester)
        if unassigned_only:
            q = q.where(MentorAssignment.id.is_(None))
        rows = (await self.session.execute(q.limit(3000))).all()
        loads = dict((await self.session.execute(select(MentorAssignment.mentor_id, func.count()).where(MentorAssignment.end_date.is_(None))
                                                  .group_by(MentorAssignment.mentor_id))).all())
        mentors = (await self.session.execute(select(Employee).where(Employee.id.in_(list(loads) or [uuid.uuid4()])).order_by(Employee.first_name))).scalars().all()
        open_refs = int((await self.session.execute(select(func.count()).select_from(MentorMeeting).where(MentorMeeting.referral_status == "OPEN",
                                                                                                         MentorMeeting.is_deleted.is_(False)))).scalar_one())
        return {"students": [{"id": s.id, "roll_number": s.roll_number, "name": f"{s.first_name} {s.last_name}", "program": code, "semester": s.current_semester,
                              "mentor_id": e.id if e else None, "mentor": f"{e.first_name} {e.last_name}" if e else None} for s, _a, e, code in rows],
                "mentors": [{"id": m.id, "name": f"{m.first_name} {m.last_name}", "mentees": int(loads[m.id])} for m in mentors],
                "open_referrals": open_refs}

    # ------------------------------------------------------------------ the mentor
    async def _may_see(self, user, student_id: uuid.UUID, *, admin: bool) -> uuid.UUID | None:
        """The mentor's employee id when allowed (None for an administrator who is not the mentor)."""
        emp = await self.employee_of(user)
        cur = await self._current(student_id)
        if cur is not None and emp is not None and cur.mentor_id == emp:
            return emp
        if admin:
            return None
        raise AuthorizationError("Only the student's mentor (or an administrator) can see this.")

    async def mentees(self, user) -> list[dict]:
        from app.academics.watchlist import _fee_overdue, for_students
        from app.programs.models import Program
        from app.students.models import Student

        emp = await self.employee_of(user)
        if emp is None:
            return []
        rows = (await self.session.execute(select(Student, Program.code).join(MentorAssignment, MentorAssignment.student_id == Student.id)
                                           .outerjoin(Program, Program.id == Student.program_id)
                                           .where(MentorAssignment.mentor_id == emp, MentorAssignment.end_date.is_(None))
                                           .order_by(Student.roll_number))).all()
        ids = {s.id for s, _ in rows}
        signs = await for_students(self.session, ids) if ids else {}
        fees = await _fee_overdue(self.session, ids) if ids else set()
        last = {sid: (d, f) for sid, d, f in (await self.session.execute(
            select(MentorMeeting.student_id, func.max(MentorMeeting.met_on), func.max(MentorMeeting.follow_up_on))
            .where(MentorMeeting.student_id.in_(ids or {uuid.uuid4()}), MentorMeeting.is_deleted.is_(False)).group_by(MentorMeeting.student_id))).all()}
        today = institute_today()
        out = []
        for s, code in rows:
            sg = [f"{x['course_code']}: " + SIGN_TEXT[x["kind"]].format(v=x["value"]) for x in signs.get(s.id, [])]
            if s.id in fees:
                sg.append("fee overdue 30+ days")
            met, fu = last.get(s.id, (None, None))
            out.append({"id": s.id, "roll_number": s.roll_number, "name": f"{s.first_name} {s.last_name}", "program": code, "semester": s.current_semester,
                        "signs": sg, "last_met": met, "follow_up_on": fu if fu and (met is None or fu >= met) else None,
                        "follow_up_due": bool(fu and fu <= today and (met is None or fu >= met))})
        out.sort(key=lambda x: (not x["follow_up_due"], -len(x["signs"]), x["roll_number"] or ""))
        return out

    async def student_file(self, student_id: uuid.UUID, user, *, admin: bool) -> dict:
        from app.academics.watchlist import _fee_overdue, for_students
        from app.employees.models import Employee
        from app.programs.models import Program
        from app.students.models import Student

        await self._may_see(user, student_id, admin=admin)
        s = await self.session.get(Student, student_id)
        if s is None:
            raise ResourceNotFoundError("Student not found.")
        p = await self.session.get(Program, s.program_id) if s.program_id else None
        cur = await self._current(s.id)
        mentor = await self.session.get(Employee, cur.mentor_id) if cur else None
        signs = (await for_students(self.session, {s.id})).get(s.id, [])
        meetings = (await self.session.execute(select(MentorMeeting, Employee).join(Employee, Employee.id == MentorMeeting.mentor_id)
                                               .where(MentorMeeting.student_id == s.id, MentorMeeting.is_deleted.is_(False))
                                               .order_by(MentorMeeting.met_on.desc(), MentorMeeting.created_at.desc()))).all()
        return {"id": s.id, "roll_number": s.roll_number, "name": f"{s.first_name} {s.last_name}", "program": p.code if p else None,
                "semester": s.current_semester, "contact_no": s.contact_no, "guardian_name": getattr(s, "guardian_name", None),
                "mentor": f"{mentor.first_name} {mentor.last_name}" if mentor else None,
                "signs": [f"{x['course_code']}: " + SIGN_TEXT[x["kind"]].format(v=x["value"]) for x in signs] +
                         (["fee overdue 30+ days"] if s.id in await _fee_overdue(self.session, {s.id}) else []),
                "meetings": [self._m(m, e) for m, e in meetings]}

    @staticmethod
    def _m(m: MentorMeeting, e) -> dict:
        return {"id": m.id, "met_on": m.met_on, "kind": m.kind, "kind_name": KINDS[m.kind], "notes": m.notes, "actions": m.actions,
                "follow_up_on": m.follow_up_on, "referral_status": m.referral_status, "referral_reason": m.referral_reason, "resolution": m.resolution,
                "mentor": f"{e.first_name} {e.last_name}" if e else None}

    async def add_meeting(self, student_id: uuid.UUID, data: dict, *, user, admin: bool) -> dict:
        emp = await self._may_see(user, student_id, admin=admin)
        if emp is None:
            raise AuthorizationError("Meetings are recorded by the student's mentor.")
        kind = str(data.get("kind") or "MEETING").upper()
        if kind not in KINDS:
            raise ValidationAppError("Meeting, phone call or with parents.")
        notes = (data.get("notes") or "").strip()
        if len(notes) < 5:
            raise ValidationAppError("Write what was discussed.")
        met_on: date = data.get("met_on") or institute_today()
        if met_on > institute_today():
            raise ValidationAppError("A meeting cannot be in the future.")
        fu = data.get("follow_up_on")
        if fu and fu <= met_on:
            raise ValidationAppError("The follow-up must be after the meeting.")
        refer = bool(data.get("refer"))
        reason = (data.get("referral_reason") or "").strip()
        if refer and len(reason) < 5:
            raise ValidationAppError("Say why you are referring the student to the principal.")
        m = MentorMeeting(student_id=student_id, mentor_id=emp, met_on=met_on, kind=kind, notes=notes[:20000], actions=(data.get("actions") or "").strip()[:1000] or None,
                          follow_up_on=fu, referral_status="OPEN" if refer else None, referral_reason=reason[:500] if refer else None, created_by=user.id)
        self.session.add(m)
        await self.session.flush()
        if refer:
            await self._tell_admins(m)
        return {"id": m.id}

    async def edit_meeting(self, meeting_id: uuid.UUID, data: dict, *, user) -> dict:
        m = await self.session.get(MentorMeeting, meeting_id)
        if m is None or m.is_deleted:
            raise ResourceNotFoundError("Meeting not found.")
        if m.created_by != user.id:
            raise AuthorizationError("Only the mentor who wrote it can change it.")
        if (datetime.now(timezone.utc) - m.created_at).days > 7:
            raise BusinessRuleViolationError("Notes can be corrected for a week; add a new meeting instead.")
        if data.get("remove"):
            m.is_deleted = True                    # kept in the database, hidden: e.g. written on the wrong student
        else:
            if data.get("notes") is not None:
                if len(data["notes"].strip()) < 5:
                    raise ValidationAppError("Write what was discussed.")
                m.notes = data["notes"].strip()[:20000]
            if "actions" in data:
                m.actions = (data["actions"] or "").strip()[:1000] or None
            if "follow_up_on" in data:
                m.follow_up_on = data["follow_up_on"]
        await self.session.flush()
        return {"id": m.id, "removed": m.is_deleted}

    async def _tell_admins(self, m: MentorMeeting) -> None:
        """Never raises."""
        try:
            async with self.session.begin_nested():
                from app.auth.models import Permission, RolePermission, UserRole
                from app.notifications.models import NotificationCategory
                from app.notifications.service import create_notification
                from app.students.models import Student

                s = await self.session.get(Student, m.student_id)
                users = set((await self.session.execute(select(UserRole.user_id).join(RolePermission, RolePermission.role_id == UserRole.role_id)
                                                        .join(Permission, Permission.id == RolePermission.permission_id)
                                                        .where(Permission.code == "mentoring.manage"))).scalars())
                for uid in users:
                    await create_notification(self.session, user_id=uid, category=NotificationCategory.GENERAL,
                                              title=f"Mentor referral: {s.first_name} {s.last_name} ({s.roll_number})",
                                              message=m.referral_reason or "", link="/dashboard/admin/mentoring#referrals")
        except Exception:                          # noqa: BLE001
            pass

    # ------------------------------------------------------------------ referrals (administration)
    async def referrals(self, *, status: str = "OPEN") -> list[dict]:
        from app.employees.models import Employee
        from app.students.models import Student

        q = (select(MentorMeeting, Student, Employee).join(Student, Student.id == MentorMeeting.student_id).join(Employee, Employee.id == MentorMeeting.mentor_id)
             .where(MentorMeeting.referral_status.is_not(None), MentorMeeting.is_deleted.is_(False)).order_by(MentorMeeting.met_on.desc()).limit(300))
        if status:
            q = q.where(MentorMeeting.referral_status == status)
        return [{**self._m(m, e), "student_id": s.id, "student": f"{s.first_name} {s.last_name}", "roll_number": s.roll_number}
                for m, s, e in (await self.session.execute(q)).all()]

    async def resolve(self, meeting_id: uuid.UUID, *, resolution: str, actor_id: uuid.UUID) -> dict:
        m = await self.session.get(MentorMeeting, meeting_id)
        if m is None or m.referral_status != "OPEN":
            raise BusinessRuleViolationError("This referral is not open.")
        if len((resolution or "").strip()) < 5:
            raise ValidationAppError("Say what was decided or done.")
        m.referral_status, m.resolution, m.resolved_by, m.resolved_at = "RESOLVED", resolution.strip()[:1000], actor_id, datetime.now(timezone.utc)
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="mentoring.referral_resolved", entity_type="student", entity_id=m.student_id)
        try:
            async with self.session.begin_nested():
                from app.employees.models import Employee
                from app.notifications.models import NotificationCategory
                from app.notifications.service import create_notification

                e = await self.session.get(Employee, m.mentor_id)
                if e and e.user_id:
                    await create_notification(self.session, user_id=e.user_id, category=NotificationCategory.GENERAL, title="Your referral was answered",
                                              message=m.resolution, link=f"/dashboard/faculty/mentees#s{m.student_id}")
        except Exception:                          # noqa: BLE001
            pass
        return {"id": m.id, "referral_status": m.referral_status}

    # ------------------------------------------------------------------ students and parents: who the mentor is
    async def mentor_name(self, student_id: uuid.UUID) -> str | None:
        from app.employees.models import Employee

        cur = await self._current(student_id)
        if cur is None:
            return None
        e = await self.session.get(Employee, cur.mentor_id)
        return f"{e.first_name} {e.last_name}" if e else None
