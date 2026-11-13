"""School mode, stage 3: the session's exams (1st Term, Mid-Term, Final), marks, report cards and promotion.

Exams
  The office creates each exam for the session; every class's subjects get a total (100 by default, changeable per
  subject, e.g. Drawing 50). Subject teachers enter marks for their sections (a student marked absent in the exam roll
  is shown as Absent; Absent counts 0). Class teachers add remarks and rate skills & behaviour (A-D). The office then
  publishes the exam class by class: students and parents are told and can open the report card (PDF).

Results
  An exam: each subject's %, grade (board scale A+ ... F; Cambridge A* ... U for O / A Levels), the total %, grade,
  position in the section (ties share: 1, 2, 2, 4), attendance from the roll call, remarks and skills.
  The year: each exam's % weighted by Settings (1st Term 20%, Mid-Term 30%, Final 50% by default; weights of exams not
  held are shared out). Pass = the overall % and every subject's % at least the pass marks in Settings (33%).

Promotion
  After the Final is published, the office opens a class: students who passed are ticked to move to the next class
  (same section letter) in the new session; the others are listed to keep back or promote on approval. Class 8 and
  Class 10 students choose a stream / group. Each decision is kept on record (school_promotions).
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import Course, CourseOffering, Enrollment, EnrollmentOutcome, EnrollmentStatus
from app.academics.terms import AcademicTerm
from app.audit.service import AuditService
from app.core.config import settings
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError, ValidationAppError
from app.core.logging import get_logger
from app.programs.models import Program
from app.school.daily import _teaches_section, can_see_student, employee_of, is_office
from app.school.models import (ClassSubject, DailyAttendance, DayStatus, ExamKind, PromotionDecision, ReportCardNote, ReportPublication, SchoolExam,
                               SchoolExamSubject, SchoolMark, SchoolPromotion, SchoolSection, SchoolSkill)
from app.school.service import SchoolService, school_mode
from app.students.models import Student, StudentStatus

logger = get_logger(__name__)
KIND_NAME = {ExamKind.FIRST_TERM: "1st Term", ExamKind.MID_TERM: "Mid-Term", ExamKind.FINAL: "Final"}
KIND_ORDER = [ExamKind.FIRST_TERM, ExamKind.MID_TERM, ExamKind.FINAL]
BOARD = [(80, "A+"), (70, "A"), (60, "B"), (50, "C"), (40, "D"), (33, "E"), (0, "F")]
CAMBRIDGE = [(90, "A*"), (80, "A"), (70, "B"), (60, "C"), (50, "D"), (40, "E"), (0, "U")]
DEFAULT_SKILLS = ["Discipline", "Punctuality", "Neatness", "Class participation", "Homework", "Behaviour with others"]
RATINGS = {"A": "Excellent", "B": "Good", "C": "Fair", "D": "Needs improvement"}


def grade(pct: float | None, stage: str | None) -> str:
    if pct is None:
        return "-"
    for cut, g in (CAMBRIDGE if stage in ("O_LEVEL", "A_LEVEL") else BOARD):
        if pct >= cut:
            return g
    return "F"


def weights() -> dict[ExamKind, float]:
    return {ExamKind.FIRST_TERM: float(settings.school_weight_first_term), ExamKind.MID_TERM: float(settings.school_weight_mid_term),
            ExamKind.FINAL: float(settings.school_weight_final)}


def _pct(obtained: float, total: float) -> float | None:
    return None if not total else round(obtained / total * 100, 2)


def _kind_from_name(name: str) -> ExamKind | None:
    n = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    if "final" in n:
        return ExamKind.FINAL
    if "mid" in n:
        return ExamKind.MID_TERM
    if "first" in n or "1st" in n:
        return ExamKind.FIRST_TERM
    return None


def _name(p) -> str:
    return f"{p.first_name} {p.last_name}".strip() if p else ""


def rank(values: dict) -> dict:
    """{key: position} by value, highest first; ties share a position (1, 2, 2, 4). None values get no position."""
    ordered = sorted(((v, k) for k, v in values.items() if v is not None), key=lambda x: -x[0])
    out, pos, last = {}, 0, None
    for i, (v, k) in enumerate(ordered, start=1):
        if v != last:
            pos, last = i, v
        out[k] = pos
    return out


class ResultsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ exams (office)
    async def _exam(self, exam_id: uuid.UUID) -> tuple[SchoolExam, AcademicTerm]:
        ex = await self.session.get(SchoolExam, exam_id)
        if ex is None:
            raise ResourceNotFoundError("Exam not found.")
        return ex, await self.session.get(AcademicTerm, ex.term_id)

    async def _classes_in(self, term: AcademicTerm) -> list[Program]:
        ids = select(SchoolSection.program_id).where(SchoolSection.term_id == term.id)
        return list((await self.session.execute(select(Program).where(Program.id.in_(ids)).order_by(Program.school_level, Program.name))).scalars())

    async def create_exam(self, *, term_label: str | None, kind: str, actor_id: uuid.UUID) -> dict:
        if not school_mode():
            raise BusinessRuleViolationError("School mode is off.")
        term = await SchoolService(self.session).term(term_label)
        k = ExamKind(kind)
        if (await self.session.execute(select(SchoolExam.id).where(SchoolExam.term_id == term.id, SchoolExam.kind == k))).first():
            raise DuplicateResourceError(f"The {KIND_NAME[k]} exam of {term.label} is already set up.")
        ex = SchoolExam(term_id=term.id, kind=k, created_by=actor_id)
        self.session.add(ex)
        await self.session.flush()
        await self._fill_subjects(ex, term)
        await self.ensure_skills()
        await self.audit.log(actor_user_id=actor_id, action="school.exam_created", entity_type="school_exam", entity_id=ex.id,
                             after={"session": term.label, "exam": KIND_NAME[k]})
        return await self.exam_view(ex.id)

    async def _fill_subjects(self, ex: SchoolExam, term: AcademicTerm) -> None:
        """Every subject of every class in the session gets a total (100) in this exam; subjects added later are added too."""
        have = set((await self.session.execute(select(SchoolExamSubject.course_id).where(SchoolExamSubject.exam_id == ex.id))).scalars())
        for p in await self._classes_in(term):
            for cs in (await self.session.execute(select(ClassSubject).where(ClassSubject.program_id == p.id))).scalars():
                if cs.course_id not in have:
                    self.session.add(SchoolExamSubject(exam_id=ex.id, course_id=cs.course_id, program_id=p.id, total_marks=100))
                    have.add(cs.course_id)
        await self.session.flush()

    async def exams(self, term_label: str | None) -> dict:
        term = await SchoolService(self.session).term(term_label)
        rows = (await self.session.execute(select(SchoolExam).where(SchoolExam.term_id == term.id))).scalars().all()
        rows = sorted(rows, key=lambda e: KIND_ORDER.index(e.kind))
        return {"session": term.label, "exams": [await self.exam_view(e.id) for e in rows],
                "can_create": [{"kind": k.value, "name": KIND_NAME[k]} for k in KIND_ORDER if k not in {e.kind for e in rows}],
                "weights": {k.value: v for k, v in weights().items()}, "pass_percent": float(settings.school_pass_percent),
                "subject_pass_percent": float(settings.school_subject_pass_percent)}

    async def exam_view(self, exam_id: uuid.UUID) -> dict:
        """The exam with each class's progress: marks entered, remarks written, published or not."""
        ex, term = await self._exam(exam_id)
        await self._fill_subjects(ex, term)
        published = set((await self.session.execute(select(ReportPublication.program_id).where(ReportPublication.exam_id == ex.id))).scalars())
        classes = []
        for p in await self._classes_in(term):
            pupils = [s for s in (await self.session.execute(select(Student).where(Student.program_id == p.id, Student.status == StudentStatus.ACTIVE)))
                      .scalars() if s.section_label]
            needed = await self._needed(ex, term, p, pupils)
            have = (await self.session.execute(select(func.count()).select_from(SchoolMark).where(
                SchoolMark.exam_id == ex.id, SchoolMark.student_id.in_([s.id for s in pupils] or [uuid.uuid4()]),
                (SchoolMark.marks.is_not(None)) | SchoolMark.absent))).scalar_one()
            notes = (await self.session.execute(select(func.count()).select_from(ReportCardNote).where(
                ReportCardNote.exam_id == ex.id, ReportCardNote.student_id.in_([s.id for s in pupils] or [uuid.uuid4()])))).scalar_one()
            classes.append({"program_id": p.id, "class": p.name, "students": len(pupils), "marks_needed": needed, "marks_entered": min(have, needed),
                            "remarks": notes, "published": p.id in published})
        return {"id": ex.id, "kind": ex.kind.value, "name": KIND_NAME[ex.kind], "session": term.label, "marks_open": ex.marks_open, "classes": classes}

    async def _needed(self, ex: SchoolExam, term: AcademicTerm, program: Program, pupils: list[Student]) -> int:
        """How many marks this class needs: each student x each subject they are enrolled in this session."""
        if not pupils:
            return 0
        courses = set((await self.session.execute(select(SchoolExamSubject.course_id).where(SchoolExamSubject.exam_id == ex.id,
                                                                                            SchoolExamSubject.program_id == program.id))).scalars())
        return (await self.session.execute(select(func.count()).select_from(Enrollment).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                                           .where(Enrollment.student_id.in_([s.id for s in pupils]), Enrollment.status.in_((EnrollmentStatus.ENROLLED, EnrollmentStatus.COMPLETED)),
                                                  CourseOffering.term_label == term.label, CourseOffering.course_id.in_(courses or {uuid.uuid4()})))).scalar_one()

    async def totals(self, exam_id: uuid.UUID, program_id: uuid.UUID) -> list[dict]:
        ex, term = await self._exam(exam_id)
        await self._fill_subjects(ex, term)
        rows = (await self.session.execute(select(SchoolExamSubject, Course, ClassSubject.sort_order).join(Course, Course.id == SchoolExamSubject.course_id)
                                           .join(ClassSubject, (ClassSubject.course_id == SchoolExamSubject.course_id) & (ClassSubject.program_id == program_id))
                                           .where(SchoolExamSubject.exam_id == ex.id, SchoolExamSubject.program_id == program_id)
                                           .order_by(ClassSubject.sort_order, Course.title))).all()
        return [{"course_id": c.id, "subject": c.title, "total_marks": es.total_marks} for es, c, _o in rows]

    async def set_totals(self, exam_id: uuid.UUID, program_id: uuid.UUID, totals: dict[uuid.UUID, int], actor_id: uuid.UUID) -> list[dict]:
        ex, _term = await self._exam(exam_id)
        if await self.session.get(ReportPublication, (ex.id, program_id)):
            raise BusinessRuleViolationError("This class's report cards are published. Withdraw them first to change the totals.")
        for cid, total in totals.items():
            if not 1 <= int(total) <= 1000:
                raise ValidationAppError("A subject's total must be between 1 and 1000.")
            es = await self.session.get(SchoolExamSubject, (ex.id, cid))
            if es is None or es.program_id != program_id:
                raise ValidationAppError("That subject is not in this class.")
            top = (await self.session.execute(select(func.max(SchoolMark.marks)).where(SchoolMark.exam_id == ex.id, SchoolMark.course_id == cid))).scalar_one()
            if top is not None and float(top) > int(total):
                raise BusinessRuleViolationError(f"A student already has {float(top):g} marks in this subject, more than {total}.")
            es.total_marks = int(total)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="school.exam_totals", entity_type="school_exam", entity_id=ex.id,
                             after={"program_id": str(program_id), "totals": {str(k): int(v) for k, v in totals.items()}})
        return await self.totals(ex.id, program_id)

    async def set_open(self, exam_id: uuid.UUID, marks_open: bool, actor_id: uuid.UUID) -> dict:
        ex, _term = await self._exam(exam_id)
        ex.marks_open = marks_open
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="school.exam_marks_" + ("opened" if marks_open else "closed"), entity_type="school_exam",
                             entity_id=ex.id)
        return await self.exam_view(ex.id)

    # ------------------------------------------------------------------ skills
    async def ensure_skills(self) -> None:
        if (await self.session.execute(select(SchoolSkill.id).limit(1))).first() is None:
            for i, n in enumerate(DEFAULT_SKILLS):
                self.session.add(SchoolSkill(name=n, sort_order=i))
            await self.session.flush()

    async def skills(self, *, all_: bool = False) -> list[dict]:
        await self.ensure_skills()
        q = select(SchoolSkill).order_by(SchoolSkill.sort_order, SchoolSkill.name)
        if not all_:
            q = q.where(SchoolSkill.active.is_(True))
        return [{"id": s.id, "name": s.name, "active": s.active} for s in (await self.session.execute(q)).scalars()]

    async def save_skills(self, items: list[dict], actor_id: uuid.UUID) -> list[dict]:
        """[{id?, name, active}] in order. Existing ones are renamed / switched off (never deleted: old cards keep them)."""
        await self.ensure_skills()
        seen = set()
        for i, it in enumerate(items):
            name = " ".join((it.get("name") or "").split())[:60]
            if len(name) < 3:
                raise ValidationAppError("Each skill needs a name.")
            if name.lower() in seen:
                raise ValidationAppError(f"{name} is in the list twice.")
            seen.add(name.lower())
            s = await self.session.get(SchoolSkill, it["id"]) if it.get("id") else None
            if s is None:
                s = SchoolSkill(name=name)
                self.session.add(s)
            s.name, s.sort_order, s.active = name, i, bool(it.get("active", True))
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="school.skills_changed", entity_type="school_skill", entity_id=None,
                             after={"skills": [x["name"] for x in items]})
        return await self.skills(all_=True)

    # ------------------------------------------------------------------ marks (subject teachers)
    async def my_sheets(self, user) -> dict:
        """The exams of the session, the section-subjects this teacher teaches, and the sections they are class teacher of."""
        from app.school.daily import HomeworkService

        try:
            term = await SchoolService(self.session).term(None)
        except ResourceNotFoundError:
            return {"exams": [], "classes": [], "class_teacher_of": []}
        exams = sorted((await self.session.execute(select(SchoolExam).where(SchoolExam.term_id == term.id))).scalars().all(), key=lambda e: KIND_ORDER.index(e.kind))
        emp = await employee_of(self.session, user)
        ct = []
        if emp is not None:
            rows = (await self.session.execute(select(SchoolSection, Program).join(Program, Program.id == SchoolSection.program_id)
                                               .where(SchoolSection.term_id == term.id, SchoolSection.class_teacher_id == emp.id))).all()
            ct = [{"section_id": s.id, "class": p.name, "section": s.label} for s, p in rows]
        return {"session": term.label, "exams": [{"id": e.id, "name": KIND_NAME[e.kind], "marks_open": e.marks_open} for e in exams],
                "classes": await HomeworkService(self.session).my_classes(user), "class_teacher_of": ct}

    async def _offering_section(self, offering_id: uuid.UUID) -> tuple[CourseOffering, SchoolSection, Program, Course]:
        from app.school.daily import section_of_offering

        off = await self.session.get(CourseOffering, offering_id)
        if off is None:
            raise ResourceNotFoundError("Class not found.")
        sec = await section_of_offering(self.session, off)
        if sec is None:
            raise BusinessRuleViolationError("This is not a school class.")
        return off, sec, await self.session.get(Program, sec.program_id), await self.session.get(Course, off.course_id)

    async def marks_sheet(self, exam_id: uuid.UUID, offering_id: uuid.UUID, user) -> dict:
        ex, term = await self._exam(exam_id)
        off, sec, program, course = await self._offering_section(offering_id)
        emp = await employee_of(self.session, user)
        office = is_office(user)
        if not office and (emp is None or off.assigned_teacher_id != emp.id):
            raise AuthorizationError("Only the subject's teacher enters its marks.")
        if off.term_label.lower() != term.label.lower():
            raise ValidationAppError("This class is not in that exam's session.")
        await self._fill_subjects(ex, term)
        es = await self.session.get(SchoolExamSubject, (ex.id, off.course_id))
        pupils = (await self.session.execute(select(Student).join(Enrollment, Enrollment.student_id == Student.id).where(
            Enrollment.course_offering_id == off.id, Enrollment.status.in_((EnrollmentStatus.ENROLLED, EnrollmentStatus.COMPLETED))).order_by(Student.roll_number))).scalars().all()
        marks = {m.student_id: m for m in (await self.session.execute(select(SchoolMark).where(SchoolMark.exam_id == ex.id,
                                                                                               SchoolMark.course_id == off.course_id))).scalars()}
        roll_absent = await self._roll_absent(ex, off)
        published = await self.session.get(ReportPublication, (ex.id, program.id)) is not None
        can_edit = not published and (ex.marks_open or office)
        return {"exam": {"id": ex.id, "name": KIND_NAME[ex.kind], "session": term.label}, "class": program.name, "section": sec.label,
                "subject": course.title, "total_marks": es.total_marks if es else 100, "can_edit": can_edit, "published": published,
                "note": None if can_edit else ("These report cards are published." if published else "Marks entry for this exam is closed by the office."),
                "students": [{"student_id": s.id, "name": _name(s), "roll_number": s.roll_number,
                              "marks": float(marks[s.id].marks) if s.id in marks and marks[s.id].marks is not None else None,
                              "absent": marks[s.id].absent if s.id in marks else s.id in roll_absent, "absent_in_roll": s.id in roll_absent,
                              "saved": s.id in marks} for s in pupils]}

    async def _roll_absent(self, ex: SchoolExam, off: CourseOffering) -> set[uuid.UUID]:
        """Students marked Absent in the exam roll of this subject's paper on a datesheet named like this exam."""
        from app.exams.models import ExamAttendance, ExamAttendanceStatus, ExamPaper, ExamSchedule, ScheduleStatus

        rows = (await self.session.execute(select(ExamAttendance.student_id, ExamSchedule.name).join(ExamPaper, ExamPaper.id == ExamAttendance.paper_id)
                                           .join(ExamSchedule, ExamSchedule.id == ExamPaper.schedule_id)
                                           .where(ExamPaper.course_offering_id == off.id, ExamAttendance.status == ExamAttendanceStatus.ABSENT,
                                                  ExamSchedule.status == ScheduleStatus.PUBLISHED))).all()
        return {sid for sid, name in rows if _kind_from_name(name) == ex.kind}

    async def save_marks(self, exam_id: uuid.UUID, offering_id: uuid.UUID, entries: list[dict], user) -> dict:
        sheet = await self.marks_sheet(exam_id, offering_id, user)
        if not sheet["can_edit"]:
            raise BusinessRuleViolationError(sheet["note"] or "These marks cannot be changed.")
        off = await self.session.get(CourseOffering, offering_id)
        total = sheet["total_marks"]
        known = {r["student_id"] for r in sheet["students"]}
        current = {m.student_id: m for m in (await self.session.execute(select(SchoolMark).where(SchoolMark.exam_id == exam_id,
                                                                                                 SchoolMark.course_id == off.course_id))).scalars()}
        changed = 0
        now = datetime.now(timezone.utc)
        for e in entries:
            sid = e["student_id"]
            if sid not in known:
                raise ValidationAppError("A student on the list is not in this class.")
            absent = bool(e.get("absent"))
            marks = None if absent or e.get("marks") is None else float(e["marks"])
            if marks is not None and not 0 <= marks <= total:
                raise ValidationAppError(f"Marks must be between 0 and {total}.")
            m = current.get(sid)
            if m is None:
                self.session.add(SchoolMark(exam_id=exam_id, student_id=sid, course_id=off.course_id, marks=marks, absent=absent, entered_by=user.id))
                changed += 1
            elif (m.marks is None and marks is not None) or (m.marks is not None and (marks is None or float(m.marks) != marks)) or m.absent != absent:
                m.marks, m.absent, m.entered_by, m.entered_at = marks, absent, user.id, now
                changed += 1
        await self.session.flush()
        if changed:
            await self.audit.log(actor_user_id=user.id, action="school.marks_entered", entity_type="school_exam", entity_id=exam_id,
                                 after={"subject": sheet["subject"], "class": sheet["class"], "section": sheet["section"], "changed": changed})
        return await self.marks_sheet(exam_id, offering_id, user)

    # ------------------------------------------------------------------ remarks & skills (class teachers)
    async def notes_sheet(self, exam_id: uuid.UUID, section_id: uuid.UUID, user) -> dict:
        ex, term = await self._exam(exam_id)
        sec, program, _t = await SchoolService(self.session)._section(section_id)
        emp = await employee_of(self.session, user)
        if not is_office(user) and (emp is None or sec.class_teacher_id != emp.id):
            raise AuthorizationError("Only the class teacher writes the report card remarks.")
        pupils = await SchoolService(self.session)._pupils(program.id, sec.label)
        notes = {n.student_id: n for n in (await self.session.execute(select(ReportCardNote).where(ReportCardNote.exam_id == ex.id,
                                                                                                   ReportCardNote.student_id.in_([p.id for p in pupils] or [uuid.uuid4()])))).scalars()}
        published = await self.session.get(ReportPublication, (ex.id, program.id)) is not None
        return {"exam": {"id": ex.id, "name": KIND_NAME[ex.kind], "session": term.label}, "class": program.name, "section": sec.label,
                "skills": await self.skills(), "ratings": RATINGS, "can_edit": not published,
                "students": [{"student_id": p.id, "name": _name(p), "roll_number": p.roll_number,
                              "remarks": notes[p.id].remarks if p.id in notes else None,
                              "skills": notes[p.id].skills if p.id in notes else {}} for p in pupils]}

    async def save_notes(self, exam_id: uuid.UUID, section_id: uuid.UUID, entries: list[dict], user) -> dict:
        sheet = await self.notes_sheet(exam_id, section_id, user)
        if not sheet["can_edit"]:
            raise BusinessRuleViolationError("These report cards are published. Ask the office to withdraw them to change remarks.")
        known = {r["student_id"] for r in sheet["students"]}
        skill_ids = {str(s["id"]) for s in sheet["skills"]}
        for e in entries:
            if e["student_id"] not in known:
                raise ValidationAppError("A student on the list is not in this section.")
            ratings = {str(k): v for k, v in (e.get("skills") or {}).items() if v}
            if any(k not in skill_ids or v not in RATINGS for k, v in ratings.items()):
                raise ValidationAppError("Rate each skill A, B, C or D.")
            n = await self.session.get(ReportCardNote, (exam_id, e["student_id"]))
            if n is None:
                n = ReportCardNote(exam_id=exam_id, student_id=e["student_id"])
                self.session.add(n)
            n.remarks = (" ".join((e.get("remarks") or "").split()) or None)
            n.remarks = n.remarks[:600] if n.remarks else None
            n.skills, n.entered_by, n.entered_at = ratings, user.id, datetime.now(timezone.utc)
        await self.session.flush()
        await self.audit.log(actor_user_id=user.id, action="school.report_remarks", entity_type="school_exam", entity_id=exam_id,
                             after={"class": sheet["class"], "section": sheet["section"], "students": len(entries)})
        return await self.notes_sheet(exam_id, section_id, user)

    # ------------------------------------------------------------------ results
    async def _student_subjects(self, ex: SchoolExam, term: AcademicTerm, student: Student) -> list[tuple[Course, int]]:
        """The subjects this student takes this session, with their total in the exam, in report-card order."""
        rows = (await self.session.execute(select(Course, SchoolExamSubject.total_marks, ClassSubject.sort_order)
                                           .join(CourseOffering, CourseOffering.course_id == Course.id)
                                           .join(Enrollment, Enrollment.course_offering_id == CourseOffering.id)
                                           .join(SchoolExamSubject, (SchoolExamSubject.course_id == Course.id) & (SchoolExamSubject.exam_id == ex.id))
                                           .join(ClassSubject, (ClassSubject.course_id == Course.id) & (ClassSubject.program_id == SchoolExamSubject.program_id))
                                           .where(Enrollment.student_id == student.id, Enrollment.status.in_((EnrollmentStatus.ENROLLED, EnrollmentStatus.COMPLETED)),
                                                  CourseOffering.term_label == term.label).order_by(ClassSubject.sort_order, Course.title))).all()
        return [(c, t) for c, t, _o in rows]

    async def exam_result(self, ex: SchoolExam, term: AcademicTerm, student: Student, program: Program) -> dict:
        subjects = await self._student_subjects(ex, term, student)
        marks = {m.course_id: m for m in (await self.session.execute(select(SchoolMark).where(SchoolMark.exam_id == ex.id,
                                                                                              SchoolMark.student_id == student.id))).scalars()}
        sub_pass = float(settings.school_subject_pass_percent)
        rows, obtained, total, complete = [], 0.0, 0, True
        for c, t in subjects:
            m = marks.get(c.id)
            got = None if m is None else (0.0 if m.absent else (float(m.marks) if m.marks is not None else None))
            if got is None:
                complete = False
            pct = _pct(got, t) if got is not None else None
            rows.append({"course_id": c.id, "subject": c.title, "total": t, "marks": None if m is None or m.absent else got, "absent": bool(m and m.absent),
                         "percentage": pct, "grade": grade(pct, program.school_stage) if pct is not None else "-",
                         "passed": None if pct is None else pct >= sub_pass})
            obtained += got or 0.0
            total += t
        pct = _pct(obtained, total) if total else None
        passed = None if not complete or pct is None else (pct >= float(settings.school_pass_percent) and all(r["passed"] for r in rows))
        return {"subjects": rows, "obtained": round(obtained, 2), "total": total, "percentage": pct, "grade": grade(pct, program.school_stage),
                "complete": complete, "passed": passed}

    async def _class_in_term(self, student: Student, term: AcademicTerm) -> tuple[Program | None, SchoolSection | None]:
        """The class and section the student was in during that session (from their subjects), so a past year's
        report card still shows that year's class after promotion."""
        off = (await self.session.execute(select(CourseOffering).join(Enrollment, Enrollment.course_offering_id == CourseOffering.id)
                                          .join(ClassSubject, ClassSubject.course_id == CourseOffering.course_id)
                                          .where(Enrollment.student_id == student.id, CourseOffering.term_label == term.label,
                                                 Enrollment.status.in_((EnrollmentStatus.ENROLLED, EnrollmentStatus.COMPLETED))).limit(1))).scalars().first()
        if off is None:
            return None, None
        from app.school.daily import section_of_offering

        sec = await section_of_offering(self.session, off)
        return (await self.session.get(Program, sec.program_id) if sec else None), sec

    async def _section_pupils(self, sec: SchoolSection, term: AcademicTerm) -> list[Student]:
        """Students who took this section's subjects in that session (still there, or since promoted)."""
        return list((await self.session.execute(select(Student).join(Enrollment, Enrollment.student_id == Student.id)
                                                .join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                                                .join(ClassSubject, (ClassSubject.course_id == CourseOffering.course_id) & (ClassSubject.program_id == sec.program_id))
                                                .where(CourseOffering.term_label == term.label, func.upper(CourseOffering.section_label) == sec.label.upper(),
                                                       Enrollment.status.in_((EnrollmentStatus.ENROLLED, EnrollmentStatus.COMPLETED)),
                                                       Student.status == StudentStatus.ACTIVE)
                                                .distinct().order_by(Student.roll_number))).scalars())

    async def section_positions(self, ex: SchoolExam, term: AcademicTerm, sec: SchoolSection, program: Program) -> dict:
        pupils = await self._section_pupils(sec, term)
        pcts = {}
        for p in pupils:
            r = await self.exam_result(ex, term, p, program)
            pcts[p.id] = r["percentage"] if r["complete"] else None
        return {"positions": rank(pcts), "of": len(pupils)}

    async def attendance_summary(self, student_id: uuid.UUID, term: AcademicTerm) -> dict:
        q = select(DailyAttendance.status, func.count()).where(DailyAttendance.student_id == student_id)
        if term.start_date:
            q = q.where(DailyAttendance.day >= term.start_date)
        if term.end_date:
            q = q.where(DailyAttendance.day <= term.end_date)
        c = dict((await self.session.execute(q.group_by(DailyAttendance.status))).all())
        present = c.get(DayStatus.PRESENT, 0) + c.get(DayStatus.LATE, 0)
        taken = present + c.get(DayStatus.ABSENT, 0)
        return {"days": taken, "present": present, "late": c.get(DayStatus.LATE, 0), "absent": c.get(DayStatus.ABSENT, 0), "leave": c.get(DayStatus.LEAVE, 0),
                "percentage": None if not taken else round(present / taken * 100, 1)}

    async def yearly(self, term: AcademicTerm, student: Student, program: Program, *, published_only: bool) -> dict:
        """Each exam's % weighted by Settings; weights of exams not held (or not published, for students and parents) are shared out."""
        exams = (await self.session.execute(select(SchoolExam).where(SchoolExam.term_id == term.id))).scalars().all()
        if published_only:
            pub = set((await self.session.execute(select(ReportPublication.exam_id).where(ReportPublication.program_id == program.id))).scalars())
            exams = [e for e in exams if e.id in pub]
        w = weights()
        per_exam, subj = [], {}
        for e in sorted(exams, key=lambda x: KIND_ORDER.index(x.kind)):
            r = await self.exam_result(e, term, student, program)
            if r["percentage"] is None:
                continue
            per_exam.append({"kind": e.kind.value, "name": KIND_NAME[e.kind], "percentage": r["percentage"], "weight": w[e.kind]})
            for s in r["subjects"]:
                if s["percentage"] is not None:
                    subj.setdefault(s["course_id"], {"subject": s["subject"], "parts": []})["parts"].append((s["percentage"], w[e.kind]))
        tw = sum(x["weight"] for x in per_exam)
        overall = round(sum(x["percentage"] * x["weight"] for x in per_exam) / tw, 2) if tw else None
        sub_pass = float(settings.school_subject_pass_percent)
        subjects = []
        for cid, v in subj.items():
            sw = sum(wt for _p, wt in v["parts"])
            pct = round(sum(p * wt for p, wt in v["parts"]) / sw, 2) if sw else None
            subjects.append({"course_id": cid, "subject": v["subject"], "percentage": pct, "grade": grade(pct, program.school_stage),
                             "passed": None if pct is None else pct >= sub_pass})
        has_final = any(x["kind"] == ExamKind.FINAL.value for x in per_exam)
        passed = None if overall is None else (overall >= float(settings.school_pass_percent) and all(s["passed"] for s in subjects if s["passed"] is not None))
        return {"exams": per_exam, "subjects": subjects, "percentage": overall, "grade": grade(overall, program.school_stage), "passed": passed,
                "final_included": has_final}

    # ------------------------------------------------------------------ report cards
    async def report_card(self, exam_id: uuid.UUID, student_id: uuid.UUID, user) -> dict:
        student = await can_see_student(self.session, user, student_id)
        ex, term = await self._exam(exam_id)
        program, sec = await self._class_in_term(student, term)
        if program is None:
            raise ResourceNotFoundError("This student was not in a school class in that session.")
        published = await self.session.get(ReportPublication, (ex.id, program.id)) is not None
        office = is_office(user)
        if not published and not office:
            emp = await employee_of(self.session, user)
            if not (emp and sec and (sec.class_teacher_id == emp.id or await _teaches_section(self.session, emp.id, sec))):
                raise ResourceNotFoundError("This report card is not published yet.")
        result = await self.exam_result(ex, term, student, program)
        pos = await self.section_positions(ex, term, sec, program) if sec else {"positions": {}, "of": 0}
        note = await self.session.get(ReportCardNote, (ex.id, student.id))
        skills = await self.skills()
        from app.students.models import StudentGuardian

        g = (await self.session.execute(select(StudentGuardian).where(StudentGuardian.student_id == student.id)
                                        .order_by(StudentGuardian.is_primary.desc()))).scalars().first()
        guardian = f"{g.name} ({g.relation})" if g else None
        out = {"exam": {"id": ex.id, "kind": ex.kind.value, "name": KIND_NAME[ex.kind]}, "session": term.label, "published": published,
               "student": {"id": student.id, "name": _name(student), "roll_number": student.roll_number, "guardian": guardian},
               "class": program.name, "section": sec.label if sec else student.section_label, "stage": program.school_stage, **result,
               "position": pos["positions"].get(student.id), "position_of": pos["of"],
               "attendance": await self.attendance_summary(student.id, term),
               "remarks": note.remarks if note else None,
               "skills": [{"name": s["name"], "rating": (note.skills or {}).get(str(s["id"])) if note else None} for s in skills],
               "pass_percent": float(settings.school_pass_percent), "subject_pass_percent": float(settings.school_subject_pass_percent)}
        if ex.kind == ExamKind.FINAL:
            out["year"] = await self.yearly(term, student, program, published_only=not office)
            promo = (await self.session.execute(select(SchoolPromotion).where(SchoolPromotion.student_id == student.id,
                                                                              SchoolPromotion.from_term_id == term.id))).scalar_one_or_none()
            if promo is not None:
                to = await self.session.get(Program, promo.to_program_id)
                out["promotion"] = {"decision": promo.decision.value, "to_class": to.name if to else ""}
        return out

    async def cards_for(self, student_id: uuid.UUID, user) -> list[dict]:
        """The published report cards of this student (every session they were in a school class)."""
        student = await can_see_student(self.session, user, student_id)
        rows = (await self.session.execute(select(SchoolExam, AcademicTerm, ReportPublication.program_id).join(AcademicTerm, AcademicTerm.id == SchoolExam.term_id)
                                           .join(ReportPublication, ReportPublication.exam_id == SchoolExam.id)
                                           .order_by(AcademicTerm.start_date.desc().nulls_last()))).all()
        out = []
        for ex, term, pid in rows:
            program = await self.session.get(Program, pid)
            taken = (await self.session.execute(select(Enrollment.id).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                                                .join(ClassSubject, (ClassSubject.course_id == CourseOffering.course_id) & (ClassSubject.program_id == pid))
                                                .where(Enrollment.student_id == student.id, CourseOffering.term_label == term.label).limit(1))).first()
            if not taken:
                continue
            r = await self.exam_result(ex, term, student, program)
            out.append({"exam_id": ex.id, "name": KIND_NAME[ex.kind], "kind": ex.kind.value, "session": term.label, "class": program.name,
                        "percentage": r["percentage"], "grade": r["grade"], "passed": r["passed"],
                        "pdf": f"/api/v1/school/students/{student.id}/report-cards/{ex.id}/pdf"})
        out.sort(key=lambda x: (x["session"], KIND_ORDER.index(ExamKind(x["kind"]))), reverse=True)
        return out

    # ------------------------------------------------------------------ publishing (office)
    async def publish(self, exam_id: uuid.UUID, program_id: uuid.UUID, actor_id: uuid.UUID) -> dict:
        ex, term = await self._exam(exam_id)
        program = await self.session.get(Program, program_id)
        if program is None:
            raise ResourceNotFoundError("Class not found.")
        if await self.session.get(ReportPublication, (ex.id, program.id)):
            raise BusinessRuleViolationError("Already published.")
        view = next((c for c in (await self.exam_view(ex.id))["classes"] if c["program_id"] == program.id), None)
        if view is None or not view["students"]:
            raise BusinessRuleViolationError("This class has no students in this session.")
        if view["marks_entered"] < view["marks_needed"]:
            raise BusinessRuleViolationError(f"{view['marks_needed'] - view['marks_entered']} marks are still missing in {program.name}. "
                                             "Every subject needs marks (or Absent) for every student first.")
        self.session.add(ReportPublication(exam_id=ex.id, program_id=program.id, published_by=actor_id))
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="school.report_cards_published", entity_type="school_exam", entity_id=ex.id,
                             after={"class": program.name, "exam": KIND_NAME[ex.kind], "session": term.label})
        await self._tell(ex, term, program)
        return await self.exam_view(ex.id)

    async def _tell(self, ex: SchoolExam, term: AcademicTerm, program: Program) -> None:
        from app.notifications.family import _notify, family

        try:
            async with self.session.begin_nested():
                pupils = [s for s in (await self.session.execute(select(Student).where(Student.program_id == program.id,
                                                                                         Student.status == StudentStatus.ACTIVE))).scalars() if s.section_label]
                fam = await family(self.session, {p.id for p in pupils})
                from app.sms.service import SmsService

                text = f"The {KIND_NAME[ex.kind]} report card ({term.label}) is ready. Open Report cards in the portal."
                for p in pupils:
                    f = fam.get(p.id)
                    if f:
                        await _notify(self.session, f["user_id"], f"{KIND_NAME[ex.kind]} report card", text, "/dashboard/student/report-cards")
                        for uid, _l in f["parents"]:
                            await _notify(self.session, uid, f"{p.first_name}'s {KIND_NAME[ex.kind]} report card", text, "/dashboard/parent/report-cards")
                    await SmsService(self.session).queue_to_guardian(p.id, event="RESULTS",
                                                                    body=f"{settings.app_name}: {p.first_name}'s {KIND_NAME[ex.kind]} report card ({term.label}) "
                                                                         "is ready in the portal.")
        except Exception as exc:  # noqa: BLE001 - published; a message problem must not undo it
            logger.warning("report_card_messages_failed", error=str(exc)[:200])

    async def withdraw(self, exam_id: uuid.UUID, program_id: uuid.UUID, reason: str, actor_id: uuid.UUID) -> dict:
        ex, _term = await self._exam(exam_id)
        pub = await self.session.get(ReportPublication, (ex.id, program_id))
        if pub is None:
            raise BusinessRuleViolationError("Not published.")
        if len((reason or "").strip()) < 4:
            raise ValidationAppError("Give the reason (kept on record).")
        await self.session.delete(pub)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="school.report_cards_withdrawn", entity_type="school_exam", entity_id=ex.id,
                             after={"program_id": str(program_id), "reason": reason.strip()[:300]})
        return await self.exam_view(ex.id)

    # ------------------------------------------------------------------ promotion (office)
    async def _next_classes(self, program: Program) -> list[Program]:
        if program.promotes_to_id:
            nxt = await self.session.get(Program, program.promotes_to_id)
            return [nxt] if nxt else []
        if program.school_level is None:
            return []
        same_track = {"MATRIC": ("MATRIC",), "INTER": ("INTER",), "O_LEVEL": ("O_LEVEL", "A_LEVEL"), "A_LEVEL": ("A_LEVEL",)}
        rows = (await self.session.execute(select(Program).where(Program.school_level == program.school_level + 1).order_by(Program.name))).scalars().all()
        if program.school_stage in ("O_LEVEL", "A_LEVEL"):
            rows = [r for r in rows if r.school_stage in same_track[program.school_stage]]
        else:
            rows = [r for r in rows if r.school_stage not in ("O_LEVEL", "A_LEVEL")]
        return list(rows)

    async def promotion_list(self, program_id: uuid.UUID, term_label: str | None) -> dict:
        term = await SchoolService(self.session).term(term_label)
        program = await self.session.get(Program, program_id)
        if program is None or program.school_level is None:
            raise ResourceNotFoundError("Class not found.")
        final = (await self.session.execute(select(SchoolExam).where(SchoolExam.term_id == term.id, SchoolExam.kind == ExamKind.FINAL))).scalar_one_or_none()
        final_published = bool(final and await self.session.get(ReportPublication, (final.id, program.id)))
        pupils = [s for s in (await self.session.execute(select(Student).where(Student.program_id == program.id, Student.status == StudentStatus.ACTIVE)
                                                         .order_by(Student.section_label, Student.roll_number))).scalars() if s.section_label]
        done = {p.student_id: p for p in (await self.session.execute(select(SchoolPromotion).where(SchoolPromotion.from_term_id == term.id))).scalars()}
        rows = []
        for s in pupils:
            y = await self.yearly(term, s, program, published_only=False)
            rows.append({"student_id": s.id, "name": _name(s), "roll_number": s.roll_number, "section": s.section_label, "percentage": y["percentage"],
                         "grade": y["grade"], "passed": y["passed"], "failed_subjects": [x["subject"] for x in y["subjects"] if x["passed"] is False],
                         "failed_course_ids": [x["course_id"] for x in y["subjects"] if x["passed"] is False],
                         "done": done[s.id].decision.value if s.id in done else None})
        later = (await self.session.execute(select(AcademicTerm).where(AcademicTerm.id != term.id, AcademicTerm.start_date > (term.start_date or datetime.min.date()))
                                            .order_by(AcademicTerm.start_date))).scalars().all()
        return {"class": {"id": program.id, "name": program.name}, "session": term.label, "final_published": final_published,
                "next_classes": [{"id": p.id, "name": p.name} for p in await self._next_classes(program)],
                "to_sessions": [t.label for t in later if t.status.value != "CLOSED"], "students": rows,
                "pass_percent": float(settings.school_pass_percent)}

    async def promote(self, program_id: uuid.UUID, *, term_label: str | None, to_term_label: str, items: list[dict], actor_id: uuid.UUID) -> dict:
        if not school_mode():
            raise BusinessRuleViolationError("School mode is off.")
        data = await self.promotion_list(program_id, term_label)
        if not data["final_published"]:
            raise BusinessRuleViolationError("Publish this class's Final report cards first.")
        from_term = await SchoolService(self.session).term(term_label)
        to_term = await SchoolService(self.session).term(to_term_label)
        if to_term.id == from_term.id or to_term.label not in data["to_sessions"]:
            raise ValidationAppError("Choose the next session (it must start after this one and be open).")
        program = await self.session.get(Program, program_id)
        allowed = {c["id"] for c in data["next_classes"]}
        rows = {r["student_id"]: r for r in data["students"]}
        svc = SchoolService(self.session)
        moved, failed = [], []
        for it in items:
            sid, decision = it["student_id"], PromotionDecision(it["decision"])
            r = rows.get(sid)
            if r is None:
                raise ValidationAppError("A student on the list is not in this class.")
            if r["done"]:
                failed.append({"student_id": sid, "name": r["name"], "reason": "already decided"})
                continue
            if decision == PromotionDecision.PROMOTED and not r["passed"]:
                raise BusinessRuleViolationError(f"{r['name']} did not pass: choose 'promote on approval' (with a note) or 'keep back'.")
            if decision == PromotionDecision.PROMOTED_ON_APPROVAL and len((it.get("note") or "").strip()) < 4:
                raise ValidationAppError(f"Write why {r['name']} is promoted without passing (kept on record).")
            to_pid = program.id if decision == PromotionDecision.KEPT_BACK else it.get("to_program_id")
            if decision != PromotionDecision.KEPT_BACK and to_pid not in allowed:
                raise ValidationAppError(f"Choose the next class for {r['name']}.")
            to_prog = await self.session.get(Program, to_pid)
            sec = await svc._ensure_section(to_term, to_prog, r["section"].upper(), {"sections": 0, "subject_classes": 0}, actor_id)
            try:
                async with self.session.begin_nested():
                    await self._finish_year(sid, from_term, set(r["failed_course_ids"]))
                    res = await svc.place(sec.id, [sid], optional_course_ids=[], actor_id=actor_id)
                    if res["failed"]:
                        raise BusinessRuleViolationError(res["failed"][0]["reason"])
                    self.session.add(SchoolPromotion(student_id=sid, from_term_id=from_term.id, to_term_id=to_term.id, from_program_id=program.id,
                                                     to_program_id=to_pid, decision=decision, percentage=r["percentage"],
                                                     note=(it.get("note") or "").strip()[:300] or None, decided_by=actor_id))
                    await self.session.flush()
                moved.append({"student_id": sid, "name": r["name"], "to_class": to_prog.name, "section": sec.label, "decision": decision.value})
            except (BusinessRuleViolationError, ValidationAppError) as e:
                failed.append({"student_id": sid, "name": r["name"], "reason": e.message})
        if moved:
            await self.audit.log(actor_user_id=actor_id, action="school.promotion", entity_type="program", entity_id=program.id,
                                 after={"from": f"{program.name} {from_term.label}", "to_session": to_term.label, "students": len(moved),
                                        "kept_back": sum(1 for m in moved if m["decision"] == "KEPT_BACK")})
            await self._tell_promotion(moved, to_term)
        return {"moved": moved, "failed": failed, "list": await self.promotion_list(program_id, term_label)}

    async def _finish_year(self, student_id: uuid.UUID, term: AcademicTerm, failed_course_ids: set) -> None:
        """Close the student's subjects of the finished session: COMPLETED, passed or failed by the year's result
        (kept on record, as for a finished college term), so the new session's subjects can be enrolled."""
        rows = (await self.session.execute(select(Enrollment).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                                           .join(ClassSubject, ClassSubject.course_id == CourseOffering.course_id)
                                           .where(Enrollment.student_id == student_id, Enrollment.status == EnrollmentStatus.ENROLLED,
                                                  CourseOffering.term_label == term.label))).scalars().unique().all()
        offs = {o.id: o.course_id for o in (await self.session.execute(select(CourseOffering).where(
            CourseOffering.id.in_([e.course_offering_id for e in rows])))).scalars()} if rows else {}
        for e in rows:
            e.status = EnrollmentStatus.COMPLETED
            e.outcome = EnrollmentOutcome.FAILED if offs.get(e.course_offering_id) in failed_course_ids else EnrollmentOutcome.PASSED
        await self.session.flush()

    async def _tell_promotion(self, moved: list[dict], to_term: AcademicTerm) -> None:
        from app.notifications.family import _notify, family

        try:
            async with self.session.begin_nested():
                fam = await family(self.session, {m["student_id"] for m in moved})
                for m in moved:
                    f = fam.get(m["student_id"])
                    if not f:
                        continue
                    if m["decision"] == "KEPT_BACK":
                        text = f"{f['first']} will repeat {m['to_class']} in {to_term.label} (section {m['section']}). Please meet the class teacher."
                    else:
                        text = f"{f['first']} is promoted to {m['to_class']}, section {m['section']}, for {to_term.label}."
                    for uid, _l in f["parents"]:
                        await _notify(self.session, uid, "New class for " + f["first"], text, "/dashboard/parent/report-cards")
                    await _notify(self.session, f["user_id"], "Your class for " + to_term.label, text, "/dashboard/student/report-cards")
        except Exception as exc:  # noqa: BLE001
            logger.warning("promotion_messages_failed", error=str(exc)[:200])
