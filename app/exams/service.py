"""Datesheets and admit cards.

The exam office builds a datesheet (one paper per course: date, time, room,
invigilator) while it is a DRAFT. It cannot be published while any clash exists:
  - a student with two papers at overlapping times,
  - a room with two papers at overlapping times (across every datesheet),
  - an invigilator in two places at once.
Once PUBLISHED, students, teachers and parents see it, and admit cards (roll-number
slips) can be printed. A changed paper is re-checked for clashes before it is saved.

Admit card rules (what an exam office in Pakistan normally applies):
  - a paper is marked NOT ELIGIBLE if attendance in that course is below the minimum
    to pass (Settings -> Institute Policy);
  - the whole card is WITHHELD while the student has an unpaid attendance fine, or
    (policy: admit_card_requires_fees_cleared = 1) overdue fees.
The exam office can still print any student's card; it shows the same status.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import Course, CourseOffering, Enrollment, EnrollmentStatus
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError, ValidationAppError
from app.exams.models import ExamPaper, ExamSchedule, ScheduleStatus


def _overlap(a: ExamPaper, b: ExamPaper) -> bool:
    return a.exam_date == b.exam_date and a.start_time < b.end_time and b.start_time < a.end_time


class ExamScheduleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------------------------------------------------------------- schedules
    async def create(self, *, term_label: str, name: str, instructions: str | None, actor_id: uuid.UUID) -> ExamSchedule:
        name = " ".join(name.split())
        if not name:
            raise ValidationAppError("Give the datesheet a name, for example Midterm.")
        from app.academics.terms import AcademicTermService

        await AcademicTermService(self.session).require_usable(term_label)
        dup = (await self.session.execute(select(ExamSchedule).where(ExamSchedule.term_label == term_label, ExamSchedule.name == name))).scalar_one_or_none()
        if dup:
            raise DuplicateResourceError(f'A "{name}" datesheet for {term_label} already exists.')
        sched = ExamSchedule(term_label=term_label, name=name, instructions=instructions, created_by=actor_id)
        self.session.add(sched)
        await self.session.flush()
        return sched

    async def get(self, schedule_id: uuid.UUID) -> ExamSchedule:
        sched = await self.session.get(ExamSchedule, schedule_id)
        if sched is None:
            raise ResourceNotFoundError("Datesheet not found.")
        return sched

    async def list(self) -> list[ExamSchedule]:
        return list((await self.session.execute(select(ExamSchedule).order_by(ExamSchedule.created_at.desc()))).scalars())

    async def papers(self, schedule_id: uuid.UUID) -> list[ExamPaper]:
        return list((await self.session.execute(select(ExamPaper).where(ExamPaper.schedule_id == schedule_id)
                                                .order_by(ExamPaper.exam_date, ExamPaper.start_time))).scalars())

    # ---------------------------------------------------------------- papers
    async def _validate(self, sched: ExamSchedule, paper: ExamPaper) -> None:
        offering = await self.session.get(CourseOffering, paper.course_offering_id)
        if offering is None:
            raise ResourceNotFoundError("Course offering not found.")
        if offering.term_label != sched.term_label:
            raise BusinessRuleViolationError(f"That course is in {offering.term_label}, not {sched.term_label}.")
        if paper.end_time <= paper.start_time:
            raise ValidationAppError("The paper must end after it starts.")
        if offering.term_start_date and paper.exam_date < offering.term_start_date:
            raise ValidationAppError("The exam date is before the term starts.")
        if offering.term_end_date and paper.exam_date > offering.term_end_date:
            raise ValidationAppError(f"The exam date is after the term ends ({offering.term_end_date:%d %b %Y}).")
        if not paper.room.strip():
            raise ValidationAppError("Give the room or hall.")

    async def add_paper(self, schedule_id: uuid.UUID, *, course_offering_id: uuid.UUID, exam_date: date, start_time, end_time,
                        room: str, invigilator_employee_id: uuid.UUID | None) -> ExamPaper:
        sched = await self.get(schedule_id)
        exists = (await self.session.execute(select(ExamPaper).where(ExamPaper.schedule_id == schedule_id,
                                                                     ExamPaper.course_offering_id == course_offering_id))).scalar_one_or_none()
        if exists:
            raise DuplicateResourceError("This course already has a paper on this datesheet. Change that paper instead.")
        paper = ExamPaper(schedule_id=schedule_id, course_offering_id=course_offering_id, exam_date=exam_date, start_time=start_time,
                          end_time=end_time, room=room.strip(), invigilator_employee_id=invigilator_employee_id)
        await self._validate(sched, paper)
        self.session.add(paper)
        await self.session.flush()
        if sched.status == ScheduleStatus.PUBLISHED:
            await self._refuse_new_clashes(sched, paper)
        return paper

    async def update_paper(self, paper_id: uuid.UUID, **changes) -> ExamPaper:
        paper = await self.session.get(ExamPaper, paper_id)
        if paper is None:
            raise ResourceNotFoundError("Paper not found.")
        sched = await self.get(paper.schedule_id)
        for k, v in changes.items():
            if v is not None or k == "invigilator_employee_id":
                setattr(paper, k, v.strip() if isinstance(v, str) else v)
        await self._validate(sched, paper)
        await self.session.flush()
        if sched.status == ScheduleStatus.PUBLISHED:
            await self._refuse_new_clashes(sched, paper)
        return paper

    async def delete_paper(self, paper_id: uuid.UUID) -> None:
        paper = await self.session.get(ExamPaper, paper_id)
        if paper is None:
            raise ResourceNotFoundError("Paper not found.")
        await self.session.delete(paper)

    # ---------------------------------------------------------------- clashes
    async def clashes(self, schedule_id: uuid.UUID) -> list[dict]:
        papers = await self.papers(schedule_id)
        if not papers:
            return []
        label = await self._labels([p.course_offering_id for p in papers])
        out: list[dict] = []
        # students: who is enrolled in each paper's course
        rows = (await self.session.execute(select(Enrollment.course_offering_id, Enrollment.student_id).where(
            Enrollment.course_offering_id.in_([p.course_offering_id for p in papers]), Enrollment.status == EnrollmentStatus.ENROLLED))).all()
        students = defaultdict(set)
        for oid, sid in rows:
            students[oid].add(sid)
        for i, a in enumerate(papers):
            for b in papers[i + 1:]:
                if not _overlap(a, b):
                    continue
                shared = students[a.course_offering_id] & students[b.course_offering_id]
                if shared:
                    out.append({"kind": "STUDENT", "papers": [label[a.course_offering_id], label[b.course_offering_id]],
                                "date": a.exam_date, "detail": f"{len(shared)} student(s) sit both papers at the same time"})
                if a.invigilator_employee_id and a.invigilator_employee_id == b.invigilator_employee_id:
                    out.append({"kind": "INVIGILATOR", "papers": [label[a.course_offering_id], label[b.course_offering_id]],
                                "date": a.exam_date, "detail": "the same invigilator is in two rooms"})
        # rooms: against every paper on those dates, in every datesheet
        dates = {p.exam_date for p in papers}
        others = list((await self.session.execute(select(ExamPaper).where(ExamPaper.exam_date.in_(dates)))).scalars())
        olabel = await self._labels([p.course_offering_id for p in others])
        seen = set()
        for a in papers:
            for b in others:
                if a.id == b.id or a.room.lower() != b.room.lower() or not _overlap(a, b):
                    continue
                key = tuple(sorted((str(a.id), str(b.id))))
                if key in seen:
                    continue
                seen.add(key)
                out.append({"kind": "ROOM", "papers": [label[a.course_offering_id], olabel[b.course_offering_id]],
                            "date": a.exam_date, "detail": f"room {a.room} is booked twice"})
        return out

    async def _refuse_new_clashes(self, sched: ExamSchedule, paper: ExamPaper) -> None:
        label = (await self._labels([paper.course_offering_id]))[paper.course_offering_id]
        mine = [c for c in await self.clashes(sched.id) if label in c["papers"]]
        if mine:
            raise BusinessRuleViolationError("This change would clash: " + "; ".join(f"{' / '.join(c['papers'])}: {c['detail']}" for c in mine),
                                             details={"clashes": [{**c, "date": str(c["date"])} for c in mine]})

    async def _labels(self, offering_ids) -> dict:
        rows = (await self.session.execute(select(CourseOffering.id, Course.code, CourseOffering.section_label)
                                           .join(Course, Course.id == CourseOffering.course_id).where(CourseOffering.id.in_(set(offering_ids))))).all()
        return {oid: f"{code}-{sec}" if sec and sec != "A" else code for oid, code, sec in rows}

    # ---------------------------------------------------------------- publish
    async def publish(self, schedule_id: uuid.UUID, *, actor_id: uuid.UUID) -> ExamSchedule:
        sched = await self.get(schedule_id)
        if not await self.papers(schedule_id):
            raise BusinessRuleViolationError("Add the papers before publishing the datesheet.")
        found = await self.clashes(schedule_id)
        if found:
            raise BusinessRuleViolationError(f"Fix {len(found)} clash(es) before publishing.", details={"clashes": [{**c, "date": str(c["date"])} for c in found]})
        sched.status = ScheduleStatus.PUBLISHED
        sched.published_at = datetime.now(timezone.utc)
        from app.audit.service import AuditService

        await AuditService(self.session).log(actor_user_id=actor_id, action="exam_schedule.published", entity_type="exam_schedule",
                                             entity_id=sched.id, after={"term": sched.term_label, "name": sched.name})
        await self._notify_students(sched)
        return sched

    async def _notify_students(self, sched: ExamSchedule) -> None:
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification
        from app.students.models import Student

        offering_ids = [p.course_offering_id for p in await self.papers(sched.id)]
        user_ids = set((await self.session.execute(select(Student.user_id).join(Enrollment, Enrollment.student_id == Student.id).where(
            Enrollment.course_offering_id.in_(offering_ids), Enrollment.status == EnrollmentStatus.ENROLLED, Student.user_id.is_not(None)))).scalars())
        from app.sms.service import SmsService

        sms = SmsService(self.session)
        student_ids = set((await self.session.execute(select(Enrollment.student_id).where(
            Enrollment.course_offering_id.in_(offering_ids), Enrollment.status == EnrollmentStatus.ENROLLED))).scalars())
        for sid in student_ids:
            await sms.queue_for_student(sid, event="DATESHEET",
                                        body=f"{settings.app_name}: {sched.name} datesheet ({sched.term_label}) published. Dates, rooms and admit card: My Exams in the portal.")
        for uid in user_ids:
            await create_notification(self.session, user_id=uid, category=NotificationCategory.RESULT,
                                      title=f"{sched.name} datesheet published",
                                      message=f"The {sched.name} datesheet for {sched.term_label} is out. See My Exams for dates, rooms and your admit card.",
                                      link="/dashboard/student/exams")

    # ---------------------------------------------------------------- what people see
    async def _paper_rows(self, papers: list[ExamPaper]) -> list[dict]:
        from app.employees.models import Employee

        if not papers:
            return []
        rows = {o.id: (o, c) for o, c in (await self.session.execute(select(CourseOffering, Course).join(Course, Course.id == CourseOffering.course_id)
                                                                      .where(CourseOffering.id.in_([p.course_offering_id for p in papers])))).all()}
        inv_ids = [p.invigilator_employee_id for p in papers if p.invigilator_employee_id]
        inv = {e.id: f"{e.first_name} {e.last_name}" for e in (await self.session.execute(select(Employee).where(Employee.id.in_(inv_ids)))).scalars()} if inv_ids else {}
        out = []
        for p in papers:
            o, c = rows[p.course_offering_id]
            out.append({"id": p.id, "course_offering_id": o.id, "course_code": c.code, "course_title": c.title, "section": o.section_label,
                        "exam_date": p.exam_date, "start_time": p.start_time.strftime("%H:%M"), "end_time": p.end_time.strftime("%H:%M"),
                        "room": p.room, "invigilator_employee_id": p.invigilator_employee_id, "invigilator": inv.get(p.invigilator_employee_id)})
        return out

    async def detail(self, schedule_id: uuid.UUID) -> dict:
        sched = await self.get(schedule_id)
        return {**self.summary(sched), "papers": await self._paper_rows(await self.papers(schedule_id)), "clashes": await self.clashes(schedule_id)}

    @staticmethod
    def summary(s: ExamSchedule) -> dict:
        return {"id": s.id, "term_label": s.term_label, "name": s.name, "status": s.status.value, "instructions": s.instructions,
                "published_at": s.published_at}

    async def for_student(self, student_id: uuid.UUID) -> list[dict]:
        """Every PUBLISHED datesheet with at least one paper for a course this student is enrolled in."""
        mine = set((await self.session.execute(select(Enrollment.course_offering_id).where(
            Enrollment.student_id == student_id, Enrollment.status == EnrollmentStatus.ENROLLED))).scalars())
        out = []
        for s in await self.list():
            if s.status != ScheduleStatus.PUBLISHED:
                continue
            papers = [p for p in await self.papers(s.id) if p.course_offering_id in mine]
            if papers:
                out.append({**self.summary(s), "papers": await self._paper_rows(papers)})
        return out

    async def for_teacher(self, employee_id: uuid.UUID) -> list[dict]:
        """Published papers of courses this person teaches, or invigilates."""
        out = []
        for s in await self.list():
            if s.status != ScheduleStatus.PUBLISHED:
                continue
            papers = []
            for p in await self.papers(s.id):
                o = await self.session.get(CourseOffering, p.course_offering_id)
                if p.invigilator_employee_id == employee_id or o.assigned_teacher_id == employee_id:
                    papers.append(p)
            if papers:
                out.append({**self.summary(s), "papers": await self._paper_rows(papers)})
        return out

    # ---------------------------------------------------------------- admit card
    async def admit_card(self, schedule_id: uuid.UUID, student_id: uuid.UUID) -> dict:
        from app.attendance.holds import unpaid_attendance_fines
        from app.attendance.service import AttendanceService
        from app.fees.assignment_service import FeeStatusCalculator, late_pause
        from app.fees.models import AssignmentStatus, FeeAssignment, FeeStructure, PayerType
        from app.programs.models import Program
        from app.students.models import Student

        sched = await self.get(schedule_id)
        if sched.status != ScheduleStatus.PUBLISHED:
            raise BusinessRuleViolationError("Admit cards are available once the datesheet is published.")
        student = await self.session.get(Student, student_id)
        if student is None:
            raise ResourceNotFoundError("Student not found.")
        mine = set((await self.session.execute(select(Enrollment.course_offering_id).where(
            Enrollment.student_id == student_id, Enrollment.status == EnrollmentStatus.ENROLLED))).scalars())
        papers = [p for p in await self.papers(schedule_id) if p.course_offering_id in mine]
        if not papers:
            raise ResourceNotFoundError("This student has no paper on this datesheet.")
        att = AttendanceService(self.session)
        rows = await self._paper_rows(papers)
        minimum = settings.attendance_eligibility_threshold_percent
        for r in rows:
            pct = await att.compute_percentage(student_id, r["course_offering_id"])
            ok = pct["total_sessions_taken"] == 0 or pct["percentage"] >= minimum
            if not ok:
                from app.attendance.shortfall import cleared, not_cleared_reason

                ok = await cleared(self.session, student_id, r["course_offering_id"], pct["percentage"])
            r["attendance_percent"] = pct["percentage"]
            r["eligible"] = ok
            r["reason"] = None if ok else await not_cleared_reason(self.session, student_id, r["course_offering_id"], pct["percentage"])

        withheld = []
        n_fines, fine_total = await unpaid_attendance_fines(self.session, student_id)
        if n_fines:
            withheld.append(f"unpaid attendance fine of Rs. {fine_total:,.0f}")
        if settings.admit_card_requires_fees_cleared:
            overdue = 0.0
            assignments = (await self.session.execute(select(FeeAssignment).where(
                FeeAssignment.payer_type == PayerType.STUDENT, FeeAssignment.payer_id == student_id, FeeAssignment.status == AssignmentStatus.ACTIVE))).scalars().all()
            from app.fees.repository import FeeAssignmentRepository

            repo = FeeAssignmentRepository(self.session)
            for a in assignments:
                structure = await self.session.get(FeeStructure, a.fee_structure_id)
                for row in FeeStatusCalculator.installments(await repo.get_ledger(a.id), float(structure.late_fee_per_day) if structure.late_fee_per_day else None,
                                                            pause=late_pause(a)):
                    if row["status"] == "OVERDUE":
                        overdue += row["remaining"]
            if overdue > 0.005:
                withheld.append(f"overdue fees of Rs. {overdue:,.0f}")
        prog = await self.session.get(Program, student.program_id) if student.program_id else None
        return {"schedule": self.summary(sched), "student": {"id": student.id, "name": f"{student.first_name} {student.last_name}",
                "roll_number": student.roll_number, "program": prog.code if prog else None, "semester": student.current_semester},
                "papers": rows, "withheld": withheld, "issued_at": datetime.now(timezone.utc)}