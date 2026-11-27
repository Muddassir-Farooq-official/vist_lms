"""Handing over a member of staff's work when they are away (suspended or on leave) or have left (terminated).

Owner decisions (1.67.0):
  * AWAY (status On leave, which is what a suspension sets): their courses stay theirs. Every class from a date until a
    date is listed; the office picks a cover for each (or one teacher for all the classes of a course) and the cover is
    paid as usual (app/teaching). When they are back at work (status Active again) the covers still to come are taken
    back and the substitutes are told.
  * LEFT (Terminated): each running course gets a new teacher (or is left without one, for later); its timetable stays
    and goes with the course. Classes already held stay in the old teacher's name.
  * Both: covers they were going to take are given back; upcoming exam duties, school class-teacher roles and mentees go
    to a chosen person (or are left empty); clinical supervision ends (LEFT). Committee seats end when they leave; while
    away the seat stays and the committee page shows them as away.
Nothing already held or recorded is changed. Every change is in the activity log as one "employee.handover".
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import ClassSession, ClassSessionStatus, Course, CourseOffering, Enrollment, EnrollmentStatus
from app.audit.service import AuditService
from app.core.clock import institute_today
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.core.timefmt import hm12
from app.employees.models import Employee, EmployeeStatus, JobRole

KINDS = ("AWAY", "LEFT")
MAX_DAYS = 120


def _name(e) -> str:
    return f"{e.first_name} {e.last_name}".strip() if e else ""


class HandoverService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def _employee(self, employee_id: uuid.UUID) -> Employee:
        e = await self.session.get(Employee, employee_id)
        if e is None:
            raise ResourceNotFoundError("Employee not found.")
        return e

    @staticmethod
    def _window(kind: str, from_date: date | None, until_date: date | None) -> tuple[date, date | None]:
        if kind not in KINDS:
            raise ValidationAppError("Choose away (suspended or on leave) or left.")
        start = from_date or institute_today()
        if kind == "AWAY":
            until = until_date or start + timedelta(days=13)
            if until < start:
                raise ValidationAppError("The last day cannot be before the first day.")
            if (until - start).days > MAX_DAYS:
                raise ValidationAppError(f"Cover at most {MAX_DAYS} days at a time; do the rest later from this page.")
            return start, until
        return start, None

    # ------------------------------------------------------------------ what they hold
    async def _courses(self, e: Employee, start: date) -> list[CourseOffering]:
        """Running courses they teach, plus running courses that lost them as teacher when they left and still have nobody."""
        from app.audit.models import AuditLog

        mine = list((await self.session.execute(select(CourseOffering).where(
            CourseOffering.assigned_teacher_id == e.id, (CourseOffering.term_end_date.is_(None)) | (CourseOffering.term_end_date >= start)))).scalars())
        lost_ids = {r for r in (await self.session.execute(select(AuditLog.entity_id).where(
            AuditLog.action.in_(("course_offering.teacher_unassigned", "course_offering.teacher_handed_over")),
            AuditLog.log_metadata["former_teacher_id"].astext == str(e.id)))).scalars() if r}
        lost = list((await self.session.execute(select(CourseOffering).where(
            CourseOffering.id.in_(lost_ids or {uuid.uuid4()}), CourseOffering.assigned_teacher_id.is_(None),
            (CourseOffering.term_end_date.is_(None)) | (CourseOffering.term_end_date >= start)))).scalars())
        seen, out = set(), []
        for o in mine + lost:
            if o.id not in seen:
                seen.add(o.id)
                out.append(o)
        return out

    async def plan(self, employee_id: uuid.UUID, *, kind: str, from_date: date | None = None, until_date: date | None = None) -> dict:
        from app.committees.models import Committee, CommitteeMember
        from app.exams.models import ExamDuty, ExamPaper
        from app.mentoring.models import MentorAssignment
        from app.school.models import SchoolSection
        from app.teaching.models import ClassCover

        e = await self._employee(employee_id)
        start, until = self._window(kind, from_date, until_date)
        offerings = await self._courses(e, start)
        courses = await self.session.execute(select(Course).where(Course.id.in_({o.course_id for o in offerings} or {uuid.uuid4()})))
        course = {c.id: c for c in courses.scalars()}
        counts = dict((await self.session.execute(select(Enrollment.course_offering_id, func.count()).where(
            Enrollment.course_offering_id.in_([o.id for o in offerings] or [uuid.uuid4()]), Enrollment.status == EnrollmentStatus.ENROLLED)
            .group_by(Enrollment.course_offering_id))).all())
        course_rows = [{"offering_id": o.id, "code": course[o.course_id].code if o.course_id in course else "?",
                        "title": course[o.course_id].title if o.course_id in course else "", "term": o.term_label, "section": o.section_label,
                        "students": int(counts.get(o.id, 0)), "combined": o.combined_group_id is not None,
                        "still_theirs": o.assigned_teacher_id == e.id} for o in offerings]

        classes = []
        if kind == "AWAY":
            sessions = list((await self.session.execute(select(ClassSession).where(
                ClassSession.course_offering_id.in_([o.id for o in offerings if o.assigned_teacher_id == e.id] or [uuid.uuid4()]),
                ClassSession.status == ClassSessionStatus.SCHEDULED, ClassSession.session_date >= start, ClassSession.session_date <= until)
                .order_by(ClassSession.session_date, ClassSession.start_time))).scalars())
            covers = {c.class_session_id: c for c in (await self.session.execute(select(ClassCover).where(
                ClassCover.class_session_id.in_([s.id for s in sessions] or [uuid.uuid4()]), ClassCover.status == "ACTIVE"))).scalars()}
            subs = {x.id: x for x in (await self.session.execute(select(Employee).where(
                Employee.id.in_({c.substitute_employee_id for c in covers.values()} or {uuid.uuid4()})))).scalars()}
            off_by = {o.id: o for o in offerings}
            seen = set()
            for s in sessions:
                o = off_by[s.course_offering_id]
                key = (o.combined_group_id or o.id, s.session_date, s.start_time)      # a combined class is one class to cover
                if key in seen:
                    continue
                seen.add(key)
                cv = covers.get(s.id)
                classes.append({"class_session_id": s.id, "offering_id": o.id, "date": s.session_date, "time": hm12(s.start_time),
                                "course": course[o.course_id].code if o.course_id in course else "?", "section": f"{o.term_label} ({o.section_label})",
                                "room": s.room, "covered_by": _name(subs.get(cv.substitute_employee_id)) if cv else None,
                                "cover_id": cv.id if cv else None})

        taking = []
        for c, cs in (await self.session.execute(select(ClassCover, ClassSession).join(ClassSession, ClassSession.id == ClassCover.class_session_id).where(
                ClassCover.substitute_employee_id == e.id, ClassCover.status == "ACTIVE", ClassCover.cover_date >= start))).all():
            taking.append({"cover_id": c.id, "description": c.description, "date": c.cover_date})

        duties = []
        for d, p in (await self.session.execute(select(ExamDuty, ExamPaper).join(ExamPaper, ExamPaper.id == ExamDuty.paper_id).where(
                ExamDuty.employee_id == e.id, ExamPaper.exam_date >= start).order_by(ExamPaper.exam_date, ExamPaper.start_time))).all():
            o = await self.session.get(CourseOffering, p.course_offering_id)
            c = await self.session.get(Course, o.course_id) if o else None
            duties.append({"duty_id": d.id, "date": p.exam_date, "time": hm12(p.start_time), "room": p.room, "paper": c.code if c else "?",
                           "role": d.role.value if hasattr(d.role, "value") else d.role})

        sections = []
        from app.academics.terms import AcademicTerm
        from app.programs.models import Program

        for sec, prog, term in (await self.session.execute(select(SchoolSection, Program, AcademicTerm).join(Program, Program.id == SchoolSection.program_id)
                                                           .join(AcademicTerm, AcademicTerm.id == SchoolSection.term_id).where(
                SchoolSection.class_teacher_id == e.id, (AcademicTerm.end_date.is_(None)) | (AcademicTerm.end_date >= start)))).all():
            sections.append({"section_id": sec.id, "label": f"{prog.name} {sec.label}", "session": term.label})

        mentees = int((await self.session.execute(select(func.count()).select_from(MentorAssignment).where(
            MentorAssignment.mentor_id == e.id, MentorAssignment.end_date.is_(None)))).scalar_one())
        committees = []
        if e.user_id:
            committees = [{"committee_id": c.id, "name": c.name, "role": m.role} for m, c in (await self.session.execute(
                select(CommitteeMember, Committee).join(Committee, Committee.id == CommitteeMember.committee_id).where(
                    CommitteeMember.user_id == e.user_id, Committee.is_active.is_(True)))).all()]

        staff = [{"id": x.id, "name": _name(x), "code": x.employee_code, "teacher": jr.default_role_code in ("TEACHER", "HOD")}
                 for x, jr in (await self.session.execute(select(Employee, JobRole).join(JobRole, JobRole.id == Employee.job_role_id).where(
                     Employee.status == EmployeeStatus.ACTIVE, Employee.id != e.id).order_by(Employee.first_name, Employee.last_name))).all()]
        return {"employee": {"id": e.id, "name": _name(e), "code": e.employee_code, "status": e.status.value}, "kind": kind,
                "from_date": start, "until_date": until, "courses": course_rows, "classes": classes, "covers_taking": taking,
                "duties": duties, "sections": sections, "mentees": mentees, "committees": committees, "staff": staff}

    # ------------------------------------------------------------------ doing it
    async def apply(self, employee_id: uuid.UUID, data: dict, *, actor_id: uuid.UUID) -> dict:
        from app.committees.models import CommitteeMember
        from app.exams.models import DutyRole, ExamDuty, ExamPaper
        from app.mentoring.models import MentorAssignment
        from app.mentoring.service import MentoringService
        from app.school.models import RollCallCover, SchoolSection
        from app.teaching.service import TeachingService

        e = await self._employee(employee_id)
        kind = data.get("kind")
        start, until = self._window(kind, data.get("from_date"), data.get("until_date"))
        plan = await self.plan(employee_id, kind=kind, from_date=start, until_date=until)
        staff_ids = {s["id"] for s in plan["staff"]}

        def pick(v, what):
            if v is None:
                return None
            if v not in staff_ids:
                raise ValidationAppError(f"Choose an active member of staff for {what}.")
            return v

        done = {"courses": 0, "covers": 0, "released": 0, "duties": 0, "sections": 0, "mentees": 0, "committees": 0, "supervision": 0}
        teaching = TeachingService(self.session)
        async with self.session.begin_nested():
            # courses: a new teacher (LEFT). The timetable goes with the course; a combined class moves together.
            if kind == "LEFT":
                from app.academics.combined import members

                known = {c["offering_id"] for c in plan["courses"]}
                for item in data.get("courses") or []:
                    if item["offering_id"] not in known:
                        raise ValidationAppError("A course on the list is not one of theirs.")
                    new = pick(item.get("teacher_id"), "a course")
                    o = await self.session.get(CourseOffering, item["offering_id"])
                    for m in await members(self.session, o):
                        if m.assigned_teacher_id in (e.id, None) and m.assigned_teacher_id != new:
                            before = m.assigned_teacher_id
                            m.assigned_teacher_id = new
                            await self.audit.log(actor_user_id=actor_id, action="course_offering.teacher_handed_over", entity_type="course_offering",
                                                 entity_id=m.id, before={"teacher": str(before) if before else None},
                                                 after={"teacher": str(new) if new else None}, metadata={"former_teacher_id": str(e.id)})
                            done["courses"] += 1
                    if new is not None:
                        await self._tell(new, "You have a new course", f"You now teach {self._course_label(plan, o.id)}, handed over from {_name(e)}. "
                                         "Its timetable is on your Timetable page.", "/dashboard/faculty/timetable")
            # classes while away: a cover each
            if kind == "AWAY":
                known = {c["class_session_id"] for c in plan["classes"]}
                for item in data.get("classes") or []:
                    if item["class_session_id"] not in known:
                        raise ValidationAppError("A class on the list is not one of theirs in these dates.")
                    sub = pick(item.get("substitute_id"), "a class")
                    if sub is None:
                        continue
                    await teaching.assign(item["class_session_id"], substitute_id=sub, amount=None, actor_id=actor_id)
                    done["covers"] += 1
            # covers they were going to take: given back
            if data.get("release_covers", True):
                for c in plan["covers_taking"]:
                    try:
                        await teaching.cancel(c["cover_id"], reason=f"{_name(e)} is {'away' if kind == 'AWAY' else 'no longer on the staff'}", actor_id=actor_id)
                        done["released"] += 1
                    except BusinessRuleViolationError:
                        pass                    # already on a payroll: stays
            # exam duties
            known = {d["duty_id"]: d for d in plan["duties"]}
            for item in data.get("duties") or []:
                if item["duty_id"] not in known:
                    raise ValidationAppError("An exam duty on the list is not theirs.")
                d = await self.session.get(ExamDuty, item["duty_id"])
                new = pick(item.get("employee_id"), "an exam duty")
                paper = await self.session.get(ExamPaper, d.paper_id)
                if new is not None and (await self.session.execute(select(ExamDuty.id).where(ExamDuty.paper_id == d.paper_id, ExamDuty.employee_id == new))).first():
                    raise BusinessRuleViolationError(f"The person chosen for the {known[d.id]['paper']} paper on {known[d.id]['date']:%d %b} is already on duty there.")
                if d.role == DutyRole.IN_CHARGE:
                    paper.invigilator_employee_id = new
                if new is None:
                    await self.session.delete(d)
                else:
                    d.employee_id, d.assigned_by, d.assigned_at = new, actor_id, datetime.now(timezone.utc)
                    await self._tell(new, "Exam duty", f"You are on duty for the {known[d.id]['paper']} paper on {known[d.id]['date']:%d %b %Y} at "
                                     f"{known[d.id]['time']}, room {known[d.id]['room']} (in place of {_name(e)}).", None)
                done["duties"] += 1
            # school class teacher
            known = {s["section_id"] for s in plan["sections"]}
            for item in data.get("sections") or []:
                if item["section_id"] not in known:
                    raise ValidationAppError("A class on the list is not theirs.")
                sec = await self.session.get(SchoolSection, item["section_id"])
                sec.class_teacher_id = pick(item.get("employee_id"), "a class teacher")
                done["sections"] += 1
            await self.session.execute(RollCallCover.__table__.delete().where(RollCallCover.employee_id == e.id, RollCallCover.day >= start))
            # mentees: to the chosen mentor(s), shared evenly; or no mentor
            if plan["mentees"] and "mentors" in data:
                students = list((await self.session.execute(select(MentorAssignment.student_id).where(
                    MentorAssignment.mentor_id == e.id, MentorAssignment.end_date.is_(None)))).scalars())
                mentors = [pick(m, "mentees") for m in (data.get("mentors") or [])]
                if mentors:
                    r = await MentoringService(self.session).assign(student_ids=students, mentor_ids=mentors, actor_id=actor_id, only_unassigned=False)
                    done["mentees"] = r["assigned"]
                else:
                    for sid in students:
                        await MentoringService(self.session).unassign(sid, actor_id=actor_id)
                    done["mentees"] = len(students)
            if kind == "LEFT":
                if e.user_id and data.get("end_committees", True):
                    r = await self.session.execute(CommitteeMember.__table__.delete().where(CommitteeMember.user_id == e.user_id))
                    done["committees"] = r.rowcount or 0
                from app.clinical.models import ClinicalSupervisor

                for sv in (await self.session.execute(select(ClinicalSupervisor).where(ClinicalSupervisor.employee_id == e.id,
                                                                                       ClinicalSupervisor.is_active.is_(True)))).scalars():
                    sv.is_active = False
                    done["supervision"] += 1
            await self.session.flush()

            # leaving: the status change itself (it also takes them off any course left with them)
            if kind == "LEFT" and data.get("terminate") and e.status != EmployeeStatus.TERMINATED:
                from app.employees.service import EmployeeService

                reason = (data.get("reason") or "").strip()
                if len(reason) < 5:
                    raise ValidationAppError("Say why they are leaving (at least 5 characters).")
                await EmployeeService(self.session).change_status(e.id, new_status=EmployeeStatus.TERMINATED, reason=reason, actor_id=actor_id,
                                                                  leaving_type=data.get("leaving_type"), left_on=data.get("left_on"),
                                                                  notice_given_on=data.get("notice_given_on"))
        await self.audit.log(actor_user_id=actor_id, action="employee.handover", entity_type="employee", entity_id=e.id,
                             after={"kind": kind, "from": str(start), "until": str(until) if until else None, **done})
        return {"done": done, "plan": await self.plan(employee_id, kind=kind, from_date=start, until_date=until)}

    @staticmethod
    def _course_label(plan: dict, offering_id) -> str:
        c = next((x for x in plan["courses"] if x["offering_id"] == offering_id), None)
        return f"{c['code']} {c['title']} ({c['term']}, section {c['section']})" if c else "a course"

    async def _tell(self, employee_id: uuid.UUID, title: str, message: str, link: str | None) -> None:
        from app.notifications.models import Notification, NotificationCategory

        x = await self.session.get(Employee, employee_id)
        if x is not None and x.user_id:
            self.session.add(Notification(user_id=x.user_id, category=NotificationCategory.TASK, title=title, message=message[:500], link=link))


async def back_at_work(session: AsyncSession, employee: Employee, *, actor_id: uuid.UUID) -> int:
    """They are back (status Active again): the covers of their classes still to come are taken back, and the substitutes told."""
    from app.notifications.models import Notification, NotificationCategory
    from app.teaching.models import ClassCover
    from app.teaching.service import TeachingService

    today = institute_today()
    covers = list((await session.execute(select(ClassCover).where(ClassCover.absent_employee_id == employee.id, ClassCover.status == "ACTIVE",
                                                                   ClassCover.cover_date > today))).scalars())
    n = 0
    for c in covers:
        try:
            await TeachingService(session).cancel(c.id, reason=f"{_name(employee)} is back at work", actor_id=actor_id)
        except BusinessRuleViolationError:
            continue
        sub = await session.get(Employee, c.substitute_employee_id)
        if sub is not None and sub.user_id:
            session.add(Notification(user_id=sub.user_id, category=NotificationCategory.TASK, title="Cover no longer needed",
                                     message=f"{c.description}: {_name(employee)} is back, so you do not need to take this class."))
        n += 1
    return n
