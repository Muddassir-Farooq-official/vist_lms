"""Exam duty, the exam roll and unfair means cases.

Duty
  The exam office puts teachers on duty for each paper: one IN_CHARGE and any number
  of ASSISTANTs. How many are needed comes from the number of students sitting the
  paper (Settings -> exam_students_per_invigilator, e.g. 1 per 30). The system
  suggests teachers who are FREE at that time: no class of theirs then (the day's class
  sessions, or the weekly timetable when that day's sessions are not made yet) and no
  other exam duty that overlaps. Busy teachers are still listed, with the reason, so the
  office can choose one on purpose (classes are often off during exams); two duties at
  the same time are refused. Duties can be set or changed until the paper starts. Once
  the datesheet is published each change is told to the teacher (portal, and SMS or
  WhatsApp when sms_on_exam_duty is on).

Exam roll
  Separate from class attendance: it never changes a course's attendance %. The roll
  lists only students who may sit the paper (enrolled, and allowed by the admit card
  rules: course attendance and any hold). Barred students are listed apart with the
  reason and cannot be marked. Teachers on duty mark it on the exam day; the exam
  office can mark or correct it any time after the datesheet is published. Absent
  students then show as "Absent" in marks entry for that course.

Cases
  A teacher on duty (or the office) reports an unfair means case with a note; the exam
  office is told and records a decision. Cases are never deleted.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import (DAY_OF_WEEK_BY_PYTHON_WEEKDAY, ClassSession, ClassSessionStatus, Course, CourseOffering, Enrollment,
                                  EnrollmentStatus, TimetableSlot)
from app.auth.models import Role, User, UserRole
from app.auth.permissions import Permissions, Roles
from app.core.clock import institute_now
from app.core.config import settings
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.employees.models import Employee, EmployeeStatus
from app.exams.models import (CaseStatus, DutyRole, ExamAttendance, ExamAttendanceStatus, ExamCase, ExamDuty, ExamPaper, ExamSchedule,
                              ScheduleStatus)

TEACHING_ROLES = (Roles.TEACHER, Roles.HOD)


def _hm(t) -> str:
    return t.strftime("%H:%M")


def _norm(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _name(e: Employee) -> str:
    return f"{e.first_name} {e.last_name}".strip()


def is_office(user) -> bool:
    return Permissions.EXAM_SCHEDULE_MANAGE in user.permissions


class ExamDutyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ helpers
    async def _paper(self, paper_id: uuid.UUID) -> tuple[ExamPaper, ExamSchedule, CourseOffering, Course]:
        paper = await self.session.get(ExamPaper, paper_id)
        if paper is None:
            raise ResourceNotFoundError("Paper not found.")
        sched = await self.session.get(ExamSchedule, paper.schedule_id)
        off = await self.session.get(CourseOffering, paper.course_offering_id)
        course = await self.session.get(Course, off.course_id)
        return paper, sched, off, course

    @staticmethod
    def _label(course: Course, off: CourseOffering) -> str:
        return course.code if not off.section_label or off.section_label == "A" else f"{course.code}-{off.section_label}"

    @staticmethod
    def started(paper: ExamPaper) -> bool:
        now = institute_now()
        return (now.date(), now.time().replace(tzinfo=None)) >= (paper.exam_date, paper.start_time)

    @classmethod
    def locked(cls, paper: ExamPaper, sched: ExamSchedule) -> bool:
        """Duties of a published paper are fixed once it starts (a draft can always be changed)."""
        return sched.status == ScheduleStatus.PUBLISHED and cls.started(paper)

    async def students_sitting(self, offering_id: uuid.UUID) -> int:
        """A combined class's paper counts the students of every section."""
        from app.academics.combined import member_ids

        return (await self.session.execute(select(func.count()).select_from(Enrollment).where(
            Enrollment.course_offering_id.in_(await member_ids(self.session, offering_id)), Enrollment.status == EnrollmentStatus.ENROLLED))).scalar_one()

    @staticmethod
    def needed(students: int) -> int:
        per = max(1, int(getattr(settings, "exam_students_per_invigilator", 30) or 30))
        return max(1, math.ceil(students / per))

    async def duties(self, paper_ids) -> dict[uuid.UUID, list[dict]]:
        """Who is on duty for each paper, the person in charge first."""
        out: dict[uuid.UUID, list[dict]] = {pid: [] for pid in paper_ids}
        if not out:
            return out
        rows = (await self.session.execute(select(ExamDuty, Employee).join(Employee, Employee.id == ExamDuty.employee_id)
                                           .where(ExamDuty.paper_id.in_(list(out))))).all()
        for d, e in sorted(rows, key=lambda r: (r[0].role != DutyRole.IN_CHARGE, _name(r[1]).lower())):
            out[d.paper_id].append({"employee_id": e.id, "name": _name(e), "employee_code": e.employee_code, "role": d.role.value})
        return out

    async def _pool(self) -> list[Employee]:
        """Active teaching staff with a portal account."""
        ids = select(UserRole.user_id).join(Role, Role.id == UserRole.role_id).where(Role.code.in_(TEACHING_ROLES))
        return list((await self.session.execute(select(Employee).join(User, User.id == Employee.user_id).where(
            Employee.status == EmployeeStatus.ACTIVE, User.is_active.is_(True), Employee.user_id.in_(ids))
            .order_by(Employee.first_name, Employee.last_name))).scalars())

    async def busy(self, paper: ExamPaper) -> dict[uuid.UUID, str]:
        """Why each busy teacher is not free while this paper runs."""
        day, start, end = paper.exam_date, paper.start_time, paper.end_time
        out: dict[uuid.UUID, str] = {}
        # other exam duties at the same time (any datesheet)
        rows = (await self.session.execute(
            select(ExamDuty.employee_id, ExamPaper, Course.code).join(ExamPaper, ExamPaper.id == ExamDuty.paper_id)
            .join(CourseOffering, CourseOffering.id == ExamPaper.course_offering_id).join(Course, Course.id == CourseOffering.course_id)
            .where(ExamPaper.exam_date == day, ExamPaper.id != paper.id, ExamPaper.start_time < end, ExamPaper.end_time > start))).all()
        for emp_id, p, code in rows:
            out[emp_id] = f"On duty: {code} paper {_hm(p.start_time)}–{_hm(p.end_time)}"
        # that day's class sessions
        sessions = (await self.session.execute(
            select(ClassSession, CourseOffering.assigned_teacher_id, Course.code).join(CourseOffering, CourseOffering.id == ClassSession.course_offering_id)
            .join(Course, Course.id == CourseOffering.course_id).where(ClassSession.session_date == day))).all()
        has_sessions = {s.course_offering_id for s, _t, _c in sessions}
        for s, teacher, code in sessions:
            if teacher and s.status != ClassSessionStatus.CANCELLED and s.start_time < end and s.end_time > start:
                out.setdefault(teacher, f"Teaching {code} {_hm(s.start_time)}–{_hm(s.end_time)}")
        # the weekly timetable, for courses whose sessions for that day are not made yet
        weekday = DAY_OF_WEEK_BY_PYTHON_WEEKDAY[day.weekday()]
        slots = (await self.session.execute(
            select(TimetableSlot, CourseOffering, Course.code).join(CourseOffering, CourseOffering.id == TimetableSlot.course_offering_id)
            .join(Course, Course.id == CourseOffering.course_id)
            .where(TimetableSlot.day_of_week == weekday, TimetableSlot.start_time < end, TimetableSlot.end_time > start,
                   CourseOffering.assigned_teacher_id.is_not(None)))).all()
        for slot, off, code in slots:
            if off.id in has_sessions:
                continue
            if (off.term_start_date and day < off.term_start_date) or (off.term_end_date and day > off.term_end_date):
                continue
            out.setdefault(off.assigned_teacher_id, f"Teaching {code} {_hm(slot.start_time)}–{_hm(slot.end_time)}")
        return out

    async def _load(self, schedule_id: uuid.UUID) -> dict[uuid.UUID, int]:
        rows = (await self.session.execute(select(ExamDuty.employee_id, func.count()).join(ExamPaper, ExamPaper.id == ExamDuty.paper_id)
                                           .where(ExamPaper.schedule_id == schedule_id).group_by(ExamDuty.employee_id))).all()
        return dict(rows)

    # ------------------------------------------------------------------ the office: suggest and assign
    async def plan(self, paper_id: uuid.UUID) -> dict:
        """The duty panel for one paper: how many are needed, who is on it, and every teacher (free ones first)."""
        paper, sched, off, course = await self._paper(paper_id)
        students = await self.students_sitting(off.id)
        need = self.needed(students)
        assigned = (await self.duties([paper.id]))[paper.id]
        on = {a["employee_id"] for a in assigned}
        busy = await self.busy(paper)
        load = await self._load(sched.id)
        people = []
        for e in await self._pool():
            reason = busy.get(e.id)
            people.append({"employee_id": e.id, "name": _name(e), "employee_code": e.employee_code, "free": reason is None, "busy_reason": reason,
                           "duties_in_datesheet": load.get(e.id, 0), "on_duty": e.id in on,
                           "teaches_this_course": off.assigned_teacher_id == e.id})
        people.sort(key=lambda p: (not p["free"], p["duties_in_datesheet"], p["name"].lower()))
        missing = max(0, need - len(assigned))
        suggested = [p["employee_id"] for p in people if p["free"] and not p["on_duty"]][:missing]
        return {"paper": {"id": paper.id, "label": self._label(course, off), "course_title": course.title, "exam_date": paper.exam_date,
                          "start_time": _hm(paper.start_time), "end_time": _hm(paper.end_time), "room": paper.room},
                "students": students, "needed": need, "per_teacher": max(1, int(settings.exam_students_per_invigilator or 30)),
                "assigned": assigned, "suggested": suggested, "teachers": people, "locked": self.locked(paper, sched),
                "published": sched.status == ScheduleStatus.PUBLISHED}

    async def assign(self, paper_id: uuid.UUID, *, employee_ids: list[uuid.UUID], in_charge_id: uuid.UUID | None, actor_id: uuid.UUID) -> dict:
        paper, sched, off, course = await self._paper(paper_id)
        if self.locked(paper, sched):
            raise BusinessRuleViolationError("This paper has started, so its duties can no longer be changed.")
        wanted = list(dict.fromkeys(employee_ids))
        if in_charge_id and in_charge_id not in wanted:
            wanted.insert(0, in_charge_id)
        if wanted and in_charge_id is None:
            in_charge_id = wanted[0]
        emps = {e.id: e for e in (await self.session.execute(select(Employee).where(Employee.id.in_(wanted)))).scalars()} if wanted else {}
        for eid in wanted:
            e = emps.get(eid)
            if e is None or e.status != EmployeeStatus.ACTIVE:
                raise ValidationAppError("Choose active staff members only.")
        busy = await self.busy(paper)
        clash = [f"{_name(emps[eid])} ({busy[eid][len('On duty: '):]})" for eid in wanted if busy.get(eid, "").startswith("On duty")]
        if clash:
            raise BusinessRuleViolationError("Already on duty at that time: " + ", ".join(clash) + ".")

        current = {d.employee_id: d for d in (await self.session.execute(select(ExamDuty).where(ExamDuty.paper_id == paper.id))).scalars()}
        before = {str(k): v.role.value for k, v in current.items()}
        added, removed, changed = [], [], []
        for eid, d in current.items():
            if eid not in wanted:
                removed.append(eid)
                await self.session.delete(d)
        for eid in wanted:
            role = DutyRole.IN_CHARGE if eid == in_charge_id else DutyRole.ASSISTANT
            d = current.get(eid)
            if d is None:
                self.session.add(ExamDuty(paper_id=paper.id, employee_id=eid, role=role, assigned_by=actor_id))
                added.append(eid)
            elif d.role != role:
                d.role = role
                changed.append(eid)
        paper.invigilator_employee_id = in_charge_id if wanted else None
        await self.session.flush()
        after = {str(eid): (DutyRole.IN_CHARGE if eid == in_charge_id else DutyRole.ASSISTANT).value for eid in wanted}
        if before != after:
            from app.audit.service import AuditService

            await AuditService(self.session).log(actor_user_id=actor_id, action="exam_duty.changed", entity_type="exam_paper", entity_id=paper.id,
                                                 before={"duties": before}, after={"duties": after, "paper": self._label(course, off)})
        if sched.status == ScheduleStatus.PUBLISHED:
            label = self._label(course, off)
            for eid in added + changed:
                role = "in charge" if eid == in_charge_id else "on duty"
                await self.tell(eid, f"Exam duty: {label} on {paper.exam_date:%d %b}",
                                f"You are {role} for the {sched.name} paper of {label} {course.title} on {paper.exam_date:%a %d %b %Y}, "
                                f"{_hm(paper.start_time)}–{_hm(paper.end_time)}, room {paper.room}.")
            for eid in removed:
                await self.tell(eid, f"Exam duty removed: {label} on {paper.exam_date:%d %b}",
                                f"You are no longer on duty for the {sched.name} paper of {label} on {paper.exam_date:%a %d %b %Y}.", sms=True)
        return await self.plan(paper.id)

    async def tell(self, employee_id: uuid.UUID, title: str, message: str, *, sms: bool = True) -> None:
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification

        e = await self.session.get(Employee, employee_id)
        if e is None:
            return
        if e.user_id:
            await create_notification(self.session, user_id=e.user_id, category=NotificationCategory.GENERAL, title=title, message=message,
                                      link="/dashboard/faculty/exams")
        if sms:
            from app.sms.service import SmsService

            await SmsService(self.session).queue_to_staff(e, event="EXAM_DUTY", body=f"{settings.app_name}: {message}")

    async def tell_on_publish(self, sched: ExamSchedule) -> None:
        """When a datesheet is published: one message to each teacher on duty, with how many papers."""
        rows = (await self.session.execute(select(ExamDuty.employee_id, ExamPaper).join(ExamPaper, ExamPaper.id == ExamDuty.paper_id)
                                           .where(ExamPaper.schedule_id == sched.id))).all()
        papers: dict[uuid.UUID, list[ExamPaper]] = {}
        for eid, p in rows:
            papers.setdefault(eid, []).append(p)
        for eid, ps in papers.items():
            ps.sort(key=lambda p: (p.exam_date, p.start_time))
            first = ps[0]
            n = len(ps)
            await self.tell(eid, f"Your {sched.name} exam duties",
                            f"You are on duty for {n} paper{'s' if n != 1 else ''} in the {sched.name} exams ({sched.term_label}), "
                            f"the first on {first.exam_date:%a %d %b} at {_hm(first.start_time)}. See Exam duties in the portal.")

    # ------------------------------------------------------------------ the roll
    async def _employee_id(self, user) -> uuid.UUID | None:
        return (await self.session.execute(select(Employee.id).where(Employee.user_id == user.id))).scalar_one_or_none()

    async def _access(self, paper: ExamPaper, sched: ExamSchedule, user) -> tuple[bool, bool]:
        """(can see, can mark now) for this person and paper."""
        if sched.status != ScheduleStatus.PUBLISHED:
            if is_office(user):
                return True, False
            raise BusinessRuleViolationError("The exam roll opens once the datesheet is published.")
        if is_office(user):
            return True, True
        emp = await self._employee_id(user)
        on = emp is not None and (await self.session.execute(select(ExamDuty.id).where(ExamDuty.paper_id == paper.id, ExamDuty.employee_id == emp))).first()
        if not on:
            raise AuthorizationError("Only the teachers on duty for this paper can open its roll.")
        return True, institute_now().date() == paper.exam_date

    async def roll(self, paper_id: uuid.UUID, user) -> dict:
        from app.students.models import Student
        from app.exams.service import ExamScheduleService

        from app.academics.combined import member_ids

        paper, sched, off, course = await self._paper(paper_id)
        _see, can_mark = await self._access(paper, sched, user)
        group = await member_ids(self.session, off.id)             # a combined class: every section sits this paper
        rows = (await self.session.execute(select(Student, Enrollment.course_offering_id).join(Enrollment, Enrollment.student_id == Student.id).where(
            Enrollment.course_offering_id.in_(group), Enrollment.status == EnrollmentStatus.ENROLLED)
            .order_by(Student.roll_number, Student.first_name))).all()
        studs = [st for st, _o in rows]
        own = {st.id: o for st, o in rows}
        marks = {a.student_id: a for a in (await self.session.execute(select(ExamAttendance).where(ExamAttendance.paper_id == paper.id))).scalars()}
        svc = ExamScheduleService(self.session)
        allowed, barred = [], []
        for s in studs:
            ok, reason, _pct = await svc.course_eligibility(s.id, own[s.id])
            reasons = ([] if ok else [reason or "attendance below the minimum"]) + await svc.withheld(s.id)
            row = {"student_id": s.id, "name": f"{s.first_name} {s.last_name}".strip(), "roll_number": s.roll_number}
            if reasons:
                barred.append({**row, "reasons": reasons})
            else:
                a = marks.get(s.id)
                allowed.append({**row, "status": a.status.value if a else None})
        cases = await self.cases(paper_id=paper.id)
        counts = {k: sum(1 for r in allowed if r["status"] == k) for k in ("PRESENT", "ABSENT", "LATE")}
        return {"paper": {"id": paper.id, "label": self._label(course, off), "course_title": course.title, "schedule": sched.name,
                          "term_label": sched.term_label, "exam_date": paper.exam_date, "start_time": _hm(paper.start_time),
                          "end_time": _hm(paper.end_time), "room": paper.room},
                "on_duty": (await self.duties([paper.id]))[paper.id], "can_mark": can_mark, "is_office": is_office(user),
                "students": allowed, "barred": barred, "counts": {**counts, "unmarked": sum(1 for r in allowed if r["status"] is None)},
                "cases": cases}

    async def mark(self, paper_id: uuid.UUID, entries: list[dict], user) -> dict:
        paper, sched, off, course = await self._paper(paper_id)
        _see, can_mark = await self._access(paper, sched, user)
        if not can_mark:
            raise BusinessRuleViolationError("The roll can be marked on the exam day only. Ask the exam office to correct it.")
        view = await self.roll(paper_id, user)
        allowed = {r["student_id"] for r in view["students"]}
        barred = {r["student_id"]: r["name"] for r in view["barred"]}
        current = {a.student_id: a for a in (await self.session.execute(select(ExamAttendance).where(ExamAttendance.paper_id == paper.id))).scalars()}
        changed = 0
        for e in entries:
            sid, status = e["student_id"], ExamAttendanceStatus(e["status"])
            if sid in barred:
                raise BusinessRuleViolationError(f"{barred[sid]} is not allowed to sit this paper, so cannot be marked.")
            if sid not in allowed:
                raise ValidationAppError("That student does not sit this paper.")
            a = current.get(sid)
            if a is None:
                self.session.add(ExamAttendance(paper_id=paper.id, student_id=sid, status=status, marked_by=user.id))
                changed += 1
            elif a.status != status:
                a.status, a.marked_by, a.marked_at = status, user.id, datetime.now(timezone.utc)
                changed += 1
        await self.session.flush()
        if changed:
            from app.audit.service import AuditService

            await AuditService(self.session).log(actor_user_id=user.id, action="exam_attendance.marked", entity_type="exam_paper", entity_id=paper.id,
                                                 after={"paper": self._label(course, off), "changed": changed})
        return await self.roll(paper_id, user)

    async def absences(self, offering_id: uuid.UUID, user, *, combined: bool = False) -> list[dict]:
        """Students marked ABSENT in a published paper of this course, for marks entry.
        combined=True: a combined class's students of every section, matched to their own section's result type."""
        from app.academics.combined import member_ids
        from app.academics.models import AssessmentType, OfferingAssessment

        off = await self.session.get(CourseOffering, offering_id)
        if off is None:
            raise ResourceNotFoundError("Course offering not found.")
        if not is_office(user) and Roles.ADMIN not in user.roles:
            emp = await self._employee_id(user)
            if emp is None or emp != off.assigned_teacher_id:
                raise AuthorizationError("Only this course's teacher can see this.")
        group = await member_ids(self.session, offering_id)
        mine = group if combined else [offering_id]
        rows = (await self.session.execute(
            select(ExamAttendance.student_id, ExamSchedule, Enrollment.id, Enrollment.course_offering_id).join(ExamPaper, ExamPaper.id == ExamAttendance.paper_id)
            .join(ExamSchedule, ExamSchedule.id == ExamPaper.schedule_id)
            .join(Enrollment, (Enrollment.student_id == ExamAttendance.student_id) & Enrollment.course_offering_id.in_(mine))
            .where(ExamPaper.course_offering_id.in_(group), ExamAttendance.status == ExamAttendanceStatus.ABSENT,
                   ExamSchedule.status == ScheduleStatus.PUBLISHED))).all()
        all_types = (await self.session.execute(select(OfferingAssessment.course_offering_id, OfferingAssessment.id, AssessmentType.id, AssessmentType.name)
                                                .join(AssessmentType, AssessmentType.id == OfferingAssessment.assessment_type_id)
                                                .where(OfferingAssessment.course_offering_id.in_(mine)))).all()
        out = []
        for sid, sched, enr, own in rows:
            types = [(oa, tid, n) for o, oa, tid, n in all_types if o == own]
            match = next((oa for oa, tid, _n in types if sched.assessment_type_id and tid == sched.assessment_type_id), None) \
                or next((oa for oa, _tid, n in types if _norm(n) == _norm(sched.name)), None)
            out.append({"student_id": sid, "enrollment_id": enr, "schedule": sched.name, "offering_assessment_id": match})
        return out

    # ------------------------------------------------------------------ cases
    async def report(self, paper_id: uuid.UUID, student_id: uuid.UUID, note: str, user) -> dict:
        paper, sched, off, course = await self._paper(paper_id)
        await self._access(paper, sched, user)
        if sched.status != ScheduleStatus.PUBLISHED:
            raise BusinessRuleViolationError("Cases can be reported once the datesheet is published.")
        note = " ".join((note or "").split())
        if len(note) < 5:
            raise ValidationAppError("Write what happened.")
        from app.academics.combined import member_ids

        enrolled = (await self.session.execute(select(Enrollment.id).where(Enrollment.course_offering_id.in_(await member_ids(self.session, off.id)),
                                                                           Enrollment.student_id == student_id,
                                                                           Enrollment.status == EnrollmentStatus.ENROLLED))).first()
        if not enrolled:
            raise ValidationAppError("That student does not sit this paper.")
        case = ExamCase(paper_id=paper.id, student_id=student_id, note=note[:1000], reported_by=user.id)
        self.session.add(case)
        await self.session.flush()
        from app.audit.service import AuditService
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification
        from app.students.models import Student

        await AuditService(self.session).log(actor_user_id=user.id, action="exam_case.reported", entity_type="exam_case", entity_id=case.id,
                                             after={"paper": self._label(course, off), "student_id": str(student_id)})
        s = await self.session.get(Student, student_id)
        who = f"{s.first_name} {s.last_name} ({s.roll_number})" if s else "a student"
        office = (await self.session.execute(select(User.id, Role.code).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id)
                                             .where(Role.code.in_((Roles.ADMIN, Roles.EXAM_CONTROLLER)), User.is_active.is_(True)))).all()
        seen = set()
        for uid, code in office:
            if uid in seen or uid == user.id:
                continue
            seen.add(uid)
            await create_notification(self.session, user_id=uid, category=NotificationCategory.GENERAL,
                                      title=f"Unfair means case: {self._label(course, off)}",
                                      message=f"Reported in the {sched.name} paper of {self._label(course, off)} on {paper.exam_date:%d %b}: {who}. {note[:160]}",
                                      link="/dashboard/exam/datesheets" if code == Roles.EXAM_CONTROLLER else "/dashboard/admin/datesheets")
        return self._case_row(case, s, self._label(course, off), sched.name, paper)

    @staticmethod
    def _case_row(c: ExamCase, s, label: str, schedule: str, paper: ExamPaper) -> dict:
        return {"id": c.id, "paper_id": c.paper_id, "paper": label, "schedule": schedule, "exam_date": paper.exam_date,
                "student_id": c.student_id, "student": f"{s.first_name} {s.last_name}".strip() if s else "", "roll_number": s.roll_number if s else None,
                "note": c.note, "reported_at": c.reported_at, "status": c.status.value, "decision": c.decision, "decided_at": c.decided_at}

    async def cases(self, *, paper_id: uuid.UUID | None = None, schedule_id: uuid.UUID | None = None, status: str | None = None) -> list[dict]:
        from app.students.models import Student

        q = (select(ExamCase, Student, ExamPaper, ExamSchedule.name, Course.code, CourseOffering.section_label)
             .join(ExamPaper, ExamPaper.id == ExamCase.paper_id).join(ExamSchedule, ExamSchedule.id == ExamPaper.schedule_id)
             .join(CourseOffering, CourseOffering.id == ExamPaper.course_offering_id).join(Course, Course.id == CourseOffering.course_id)
             .join(Student, Student.id == ExamCase.student_id, isouter=True).order_by(ExamCase.reported_at.desc()))
        if paper_id:
            q = q.where(ExamCase.paper_id == paper_id)
        if schedule_id:
            q = q.where(ExamPaper.schedule_id == schedule_id)
        if status:
            q = q.where(ExamCase.status == CaseStatus(status))
        return [self._case_row(c, s, code if not sec or sec == "A" else f"{code}-{sec}", name, p)
                for c, s, p, name, code, sec in (await self.session.execute(q)).all()]

    async def decide(self, case_id: uuid.UUID, decision: str, actor_id: uuid.UUID) -> dict:
        c = await self.session.get(ExamCase, case_id)
        if c is None:
            raise ResourceNotFoundError("Case not found.")
        if c.status == CaseStatus.DECIDED:
            raise BusinessRuleViolationError("This case is already decided.")
        decision = " ".join((decision or "").split())
        if len(decision) < 5:
            raise ValidationAppError("Write the decision.")
        c.status, c.decision, c.decided_by, c.decided_at = CaseStatus.DECIDED, decision[:1000], actor_id, datetime.now(timezone.utc)
        await self.session.flush()
        from app.audit.service import AuditService

        await AuditService(self.session).log(actor_user_id=actor_id, action="exam_case.decided", entity_type="exam_case", entity_id=c.id,
                                             after={"decision": c.decision})
        return next(r for r in await self.cases(paper_id=c.paper_id) if r["id"] == c.id)

    # ------------------------------------------------------------------ a teacher's own duties
    async def mine(self, employee_id: uuid.UUID) -> list[dict]:
        rows = (await self.session.execute(
            select(ExamDuty, ExamPaper, ExamSchedule, CourseOffering, Course).join(ExamPaper, ExamPaper.id == ExamDuty.paper_id)
            .join(ExamSchedule, ExamSchedule.id == ExamPaper.schedule_id).join(CourseOffering, CourseOffering.id == ExamPaper.course_offering_id)
            .join(Course, Course.id == CourseOffering.course_id)
            .where(ExamDuty.employee_id == employee_id, ExamSchedule.status == ScheduleStatus.PUBLISHED)
            .order_by(ExamPaper.exam_date, ExamPaper.start_time))).all()
        others = await self.duties([p.id for _d, p, _s, _o, _c in rows])
        today = institute_now().date()
        return [{"paper_id": p.id, "schedule": s.name, "term_label": s.term_label, "label": self._label(c, o), "course_title": c.title,
                 "exam_date": p.exam_date, "start_time": _hm(p.start_time), "end_time": _hm(p.end_time), "room": p.room, "role": d.role.value,
                 "with": [x for x in others[p.id] if x["employee_id"] != employee_id], "is_today": p.exam_date == today,
                 "is_past": p.exam_date < today} for d, p, s, o, c in rows]
