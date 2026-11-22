"""Substitute teachers.

When a teacher's leave is approved, every class of theirs from today to the end of the leave gets a row here, with a
free teacher PROPOSED: no class or exam duty of their own then, not already substituting at that time, fewest
substitute classes this week first. The office (timetable / administration) confirms, picks someone else, or cancels
the class. A confirmed substitute is told and can mark that one class's attendance.

Never changes the timetable or the leave; cancelling a class sets that one session to CANCELLED with a reason."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.substitutes.models import ClassSubstitute

logger = structlog.get_logger(__name__)


def _name(e) -> str:
    return f"{e.first_name} {e.last_name}"


class SubstituteService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _busy(self, day: date, start, end, exclude_session: uuid.UUID | None = None) -> dict:
        """Teachers not free then: their own classes, exam duties, and classes they already substitute."""
        from app.academics.models import ClassSession
        from app.exams.duty import ExamDutyService
        from app.exams.models import ExamPaper

        probe = ExamPaper(exam_date=day, start_time=start, end_time=end, id=uuid.uuid4())
        busy = await ExamDutyService(self.session).busy(probe)
        rows = (await self.session.execute(select(ClassSubstitute.substitute_employee_id).join(ClassSession, ClassSession.id == ClassSubstitute.class_session_id).where(
            ClassSubstitute.substitute_employee_id.is_not(None), ClassSubstitute.status.in_(("PROPOSED", "CONFIRMED")), ClassSession.session_date == day,
            ClassSession.start_time < end, ClassSession.end_time > start, ClassSubstitute.class_session_id != (exclude_session or uuid.uuid4())))).scalars()
        for eid in rows:
            busy.setdefault(eid, "Already substituting then")
        return busy

    async def _load(self, day: date) -> dict:
        from app.academics.models import ClassSession

        monday = day - timedelta(days=day.weekday())
        return dict((await self.session.execute(select(ClassSubstitute.substitute_employee_id, func.count()).join(ClassSession, ClassSession.id == ClassSubstitute.class_session_id)
                                                .where(ClassSubstitute.status.in_(("PROPOSED", "CONFIRMED")), ClassSession.session_date >= monday,
                                                       ClassSession.session_date < monday + timedelta(days=7)).group_by(ClassSubstitute.substitute_employee_id))).all())

    async def candidates(self, class_session_id: uuid.UUID) -> list[dict]:
        from app.academics.models import ClassSession, CourseOffering
        from app.exams.duty import ExamDutyService

        cs = await self.session.get(ClassSession, class_session_id)
        if cs is None:
            raise ResourceNotFoundError("Class not found.")
        off = await self.session.get(CourseOffering, cs.course_offering_id)
        busy = await self._busy(cs.session_date, cs.start_time, cs.end_time, exclude_session=cs.id)
        load = await self._load(cs.session_date)
        out = []
        for e in await ExamDutyService(self.session)._pool():
            if e.id == off.assigned_teacher_id:
                continue
            out.append({"employee_id": e.id, "name": _name(e), "code": e.employee_code, "free": e.id not in busy, "busy_reason": busy.get(e.id),
                        "substitutions_this_week": int(load.get(e.id, 0))})
        out.sort(key=lambda x: (not x["free"], x["substitutions_this_week"], x["name"].lower()))
        return out

    async def propose_for_leave(self, leave) -> int:
        """Called when a leave is approved. Never raises (a problem here must not undo the approval)."""
        try:
            async with self.session.begin_nested():
                return await self._propose(leave)
        except Exception as exc:  # noqa: BLE001
            logger.warning("substitute_proposal_failed", error=str(exc)[:300])
            return 0

    async def _propose(self, leave) -> int:
        from app.academics.models import ClassSession, ClassSessionStatus, Course, CourseOffering
        from app.backups.router import _admins
        from app.core.clock import institute_today
        from app.employees.models import Employee
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification

        first = max(leave.first_day, institute_today())
        rows = (await self.session.execute(select(ClassSession).join(CourseOffering, CourseOffering.id == ClassSession.course_offering_id).where(
            CourseOffering.assigned_teacher_id == leave.employee_id, ClassSession.session_date >= first, ClassSession.session_date <= leave.last_day,
            ClassSession.status == ClassSessionStatus.SCHEDULED).order_by(ClassSession.session_date, ClassSession.start_time))).scalars().all()
        made = 0
        for cs in rows:
            if await self.session.get(ClassSubstitute, cs.id) is not None:
                continue
            cands = [c for c in await self.candidates(cs.id) if c["free"]]
            self.session.add(ClassSubstitute(class_session_id=cs.id, leave_request_id=leave.id, absent_employee_id=leave.employee_id,
                                             substitute_employee_id=cands[0]["employee_id"] if cands else None, status="PROPOSED"))
            await self.session.flush()
            made += 1
        if made:
            emp = await self.session.get(Employee, leave.employee_id)
            for a in await _admins(self.session):
                await create_notification(self.session, user_id=a.id, category=NotificationCategory.GENERAL, title=f"Substitutes needed: {made} class(es)",
                                          message=f"{_name(emp)} is on leave {leave.first_day:%d %b}–{leave.last_day:%d %b}. Check the proposed substitutes.",
                                          link="/dashboard/admin/substitutes")
        return made

    # ------------------------------------------------------------------ the office
    async def listing(self, *, status: str | None = None, from_day: date | None = None) -> list[dict]:
        from app.academics.models import ClassSession, Course, CourseOffering
        from app.core.clock import institute_today
        from app.employees.models import Employee

        from_day = from_day or institute_today()
        q = (select(ClassSubstitute, ClassSession, CourseOffering, Course).join(ClassSession, ClassSession.id == ClassSubstitute.class_session_id)
             .join(CourseOffering, CourseOffering.id == ClassSession.course_offering_id).join(Course, Course.id == CourseOffering.course_id)
             .where(ClassSession.session_date >= from_day).order_by(ClassSession.session_date, ClassSession.start_time))
        if status:
            q = q.where(ClassSubstitute.status == status)
        rows = (await self.session.execute(q.limit(500))).all()
        ids = {x for r in rows for x in (r[0].absent_employee_id, r[0].substitute_employee_id) if x}
        people = {e.id: e for e in (await self.session.execute(select(Employee).where(Employee.id.in_(ids)))).scalars()} if ids else {}
        return [{"class_session_id": s.class_session_id, "date": cs.session_date, "start": cs.start_time.strftime("%H:%M"), "end": cs.end_time.strftime("%H:%M"),
                 "room": cs.room, "course": f"{c.code} — {c.title}", "section": o.section_label, "term_label": o.term_label, "status": s.status,
                 "absent": _name(people[s.absent_employee_id]) if s.absent_employee_id in people else "?",
                 "substitute_id": s.substitute_employee_id, "substitute": _name(people[s.substitute_employee_id]) if s.substitute_employee_id in people else None,
                 "note": s.note} for s, cs, o, c in rows]

    async def decide(self, class_session_id: uuid.UUID, *, action: str, employee_id: uuid.UUID | None, note: str | None, actor_id: uuid.UUID) -> dict:
        """action: CONFIRM (with employee_id, or the proposed one) or CANCEL (no class)."""
        from app.academics.models import ClassSession, ClassSessionStatus, Course, CourseOffering
        from app.audit.service import AuditService
        from app.employees.models import Employee
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification

        row = await self.session.get(ClassSubstitute, class_session_id)
        if row is None:
            raise ResourceNotFoundError("No substitute is needed for this class.")
        cs = await self.session.get(ClassSession, class_session_id)
        from app.core.clock import institute_now

        now = institute_now()
        if (cs.session_date, cs.start_time) <= (now.date(), now.time().replace(tzinfo=None)) and action != "CONFIRM":
            raise BusinessRuleViolationError("This class has started.")
        off = await self.session.get(CourseOffering, cs.course_offering_id)
        course = await self.session.get(Course, off.course_id)
        before = {"status": row.status, "substitute": str(row.substitute_employee_id) if row.substitute_employee_id else None}
        if action == "CANCEL":
            row.status, row.substitute_employee_id = "CANCELLED", None
            cs.status = ClassSessionStatus.CANCELLED
            cs.cancelled_reason = ("Teacher on leave" + (f": {note.strip()}" if note and note.strip() else ""))[:200]
        elif action == "CONFIRM":
            sub = employee_id or row.substitute_employee_id
            if sub is None:
                raise ValidationAppError("Choose the teacher who takes this class.")
            if sub == off.assigned_teacher_id:
                raise ValidationAppError("That is the teacher on leave.")
            busy = await self._busy(cs.session_date, cs.start_time, cs.end_time, exclude_session=cs.id)
            if sub in busy and not (note and note.strip()):
                raise BusinessRuleViolationError(f"Not free then ({busy[sub]}). Write a note to choose them anyway.")
            changed = sub != row.substitute_employee_id or row.status != "CONFIRMED"
            row.status, row.substitute_employee_id = "CONFIRMED", sub
            e = await self.session.get(Employee, sub)
            if changed and e and e.user_id:
                await create_notification(self.session, user_id=e.user_id, category=NotificationCategory.GENERAL, title=f"Substitute class: {course.code}",
                                          message=f"Please take {course.code} {course.title[:50]} (section {off.section_label}) on {cs.session_date:%a %d %b} "
                                                  f"{cs.start_time:%H:%M}–{cs.end_time:%H:%M} in {cs.room}, and mark its attendance.",
                                          link="/dashboard/faculty/substitutes")
        else:
            raise ValidationAppError("Choose confirm or cancel.")
        row.note = (note or "").strip()[:300] or None
        row.decided_by, row.decided_at = actor_id, datetime.now(timezone.utc)
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action=f"substitute.{action.lower()}", entity_type="class_session", entity_id=cs.id,
                                             before=before, after={"status": row.status, "substitute": str(row.substitute_employee_id) if row.substitute_employee_id else None})
        return {"status": row.status, "substitute_id": row.substitute_employee_id}

    # ------------------------------------------------------------------ the substitute
    async def mine(self, employee_id: uuid.UUID) -> list[dict]:
        from app.academics.models import ClassSession, Course, CourseOffering
        from app.core.clock import institute_today

        rows = (await self.session.execute(select(ClassSubstitute, ClassSession, CourseOffering, Course).join(ClassSession, ClassSession.id == ClassSubstitute.class_session_id)
                                           .join(CourseOffering, CourseOffering.id == ClassSession.course_offering_id).join(Course, Course.id == CourseOffering.course_id)
                                           .where(ClassSubstitute.substitute_employee_id == employee_id, ClassSubstitute.status == "CONFIRMED",
                                                  ClassSession.session_date >= institute_today() - timedelta(days=7))
                                           .order_by(ClassSession.session_date, ClassSession.start_time))).all()
        return [{"class_session_id": cs.id, "date": cs.session_date, "start": cs.start_time.strftime("%H:%M"), "end": cs.end_time.strftime("%H:%M"), "room": cs.room,
                 "course": f"{c.code} — {c.title}", "section": o.section_label, "marked": cs.attendance_taken_at is not None} for _s, cs, o, c in rows]

    async def is_substitute(self, class_session_id: uuid.UUID, employee_id: uuid.UUID | None) -> bool:
        if employee_id is None:
            return False
        row = await self.session.get(ClassSubstitute, class_session_id)
        return row is not None and row.status == "CONFIRMED" and row.substitute_employee_id == employee_id
