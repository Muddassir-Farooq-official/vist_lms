"""School mode: set up classes, sections, subjects and teachers for a session, and place students.

Everything here builds on the normal records, so attendance, marks, timetable, fees and reports keep working:
  class    = Program (school_level set, one "semester": every student is at semester 1 of their class)
  subject  = Course linked to the class (CourseProgram semester 1) + ClassSubject (order, optional)
  session  = AcademicTerm ("2026-27")
  section  = SchoolSection (class teacher, room, seats) + one CourseOffering per subject (subject teacher)
  a pupil  = Student.program_id + Student.section_label, enrolled in the section's subjects

Set-up is safe to run again: anything that already exists is kept and only what is missing is added.
"""
from __future__ import annotations

import re
import uuid
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import ClassSession, Course, CourseOffering, CourseProgram, Enrollment, EnrollmentStatus
from app.academics.terms import AcademicTerm, AcademicTermService, TermStatus
from app.audit.service import AuditService
from app.auth.models import Role, User, UserRole
from app.auth.permissions import Roles
from app.core.config import settings
from app.core.exceptions import AppError, BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError, ValidationAppError
from app.employees.models import Employee, EmployeeStatus
from app.programs.models import Program
from app.school import catalogue
from app.school.models import ClassSubject, SchoolSection
from app.students.models import Student, StudentStatus

SUBJECT_CREDIT = 1          # school subjects carry no credit hours; 1 keeps the credit-hour rules out of the way
CLASS_CREDIT_LIMIT = 60


def school_mode() -> bool:
    return bool(int(getattr(settings, "school_mode", 0) or 0))


def _label(text: str) -> str:
    return " ".join((text or "").split())


def _name(e: Employee | None) -> str | None:
    return f"{e.first_name} {e.last_name}".strip() if e else None


class SchoolService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ guards
    @staticmethod
    def require_school_mode() -> None:
        if not school_mode():
            raise BusinessRuleViolationError("School mode is off. Switch it on in Settings → Institute type first.")

    async def term(self, label: str | None) -> AcademicTerm:
        """The session by its label; otherwise the newest open session that has school sections (or the newest open one)."""
        if label:
            t = await AcademicTermService(self.session).by_label(_label(label))
            if t is None:
                raise ResourceNotFoundError(f'The session "{label}" is not set up.')
            return t
        order = (AcademicTerm.start_date.desc().nulls_last(), AcademicTerm.created_at.desc())
        with_sections = select(SchoolSection.term_id)
        t = (await self.session.execute(select(AcademicTerm).where(AcademicTerm.status == TermStatus.OPEN, AcademicTerm.id.in_(with_sections))
                                        .order_by(*order))).scalars().first() \
            or (await self.session.execute(select(AcademicTerm).where(AcademicTerm.status == TermStatus.OPEN).order_by(*order))).scalars().first()
        if t is None:
            raise ResourceNotFoundError("No session yet. Set one up first.")
        return t

    async def _class(self, program_id: uuid.UUID) -> Program:
        p = await self.session.get(Program, program_id)
        if p is None or p.school_level is None:
            raise ResourceNotFoundError("Class not found.")
        return p

    async def _section(self, section_id: uuid.UUID) -> tuple[SchoolSection, Program, AcademicTerm]:
        sec = await self.session.get(SchoolSection, section_id)
        if sec is None:
            raise ResourceNotFoundError("Section not found.")
        return sec, await self.session.get(Program, sec.program_id), await self.session.get(AcademicTerm, sec.term_id)

    # ------------------------------------------------------------------ set-up
    async def setup(self, *, term_label: str, start_date: date | None, end_date: date | None, groups: list[str],
                    sections: list[str], actor_id: uuid.UUID) -> dict:
        self.require_school_mode()
        unknown = [g for g in groups if g not in catalogue.GROUP_BY_KEY]
        if unknown or not groups:
            raise ValidationAppError("Choose the classes to set up.")
        labels = [x for x in dict.fromkeys(_label(s).upper() for s in sections) if x] or ["A"]
        if any(len(x) > 30 for x in labels):
            raise ValidationAppError("A section name can be at most 30 letters.")

        terms = AcademicTermService(self.session)
        term = await terms.by_label(_label(term_label))
        if term is None:
            term = await terms.create(label=term_label, start_date=start_date, end_date=end_date, actor_id=actor_id)
        elif term.status == TermStatus.CLOSED:
            raise BusinessRuleViolationError(f'The session "{term.label}" is closed. Choose an open session.')

        made = {"classes": 0, "subjects": 0, "sections": 0, "subject_classes": 0}
        by_code: dict[str, Program] = {}
        skipped: list[str] = []
        for g in groups:
            for c in catalogue.GROUP_BY_KEY[g]["classes"]:
                program = await self._ensure_class(c, made, actor_id)
                if program is None:
                    skipped.append(c["code"])
                    continue
                by_code[c["code"]] = program
                for i, s in enumerate(c["subjects"]):
                    await self._ensure_subject(program, s["code"], s["title"], i, s["optional"], made, actor_id)
                for lab in labels:
                    await self._ensure_section(term, program, lab, made, actor_id)
        # the next class, for promotion (also links classes set up on an earlier run)
        all_classes = {p.code: p for p in (await self.session.execute(select(Program).where(Program.school_level.is_not(None)))).scalars()}
        for g in catalogue.GROUPS:
            for c in g["classes"]:
                p, nxt = all_classes.get(c["code"]), all_classes.get(c["next"] or "")
                if p is not None and nxt is not None and p.promotes_to_id is None:
                    p.promotes_to_id = nxt.id
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="school.setup", entity_type="academic_term", entity_id=term.id,
                             after={"session": term.label, "groups": groups, "sections": labels, **made})
        return {"session": term.label, "created": made, "skipped": skipped}

    async def _ensure_class(self, c: dict, made: dict, actor_id) -> Program | None:
        p = (await self.session.execute(select(Program).where(func.upper(Program.code) == c["code"]))).scalar_one_or_none()
        if p is not None:
            return p if p.school_level is not None else None      # a college program with the same code is left alone
        if (await self.session.execute(select(Program.id).where(func.lower(func.trim(Program.name)) == c["name"].lower()))).first():
            return None
        p = Program(code=c["code"], name=c["name"], duration_years=1, total_semesters=1, max_credit_hours_per_semester=CLASS_CREDIT_LIMIT,
                    school_level=c["level"], school_stage=c["stage"], stream=c["stream"])
        self.session.add(p)
        await self.session.flush()
        made["classes"] += 1
        await self.audit.log(actor_user_id=actor_id, action="school.class_created", entity_type="program", entity_id=p.id, after={"code": p.code})
        return p

    async def _ensure_subject(self, program: Program, code: str, title: str, order: int, optional: bool, made: dict, actor_id) -> Course:
        course_code = f"{program.code}-{code}"[:20].upper()
        course = (await self.session.execute(select(Course).where(Course.code == course_code))).scalar_one_or_none()
        if course is None:
            course = Course(code=course_code, title=title, credit_hours=SUBJECT_CREDIT, description=f"{title} for {program.name}")
            self.session.add(course)
            await self.session.flush()
            made["subjects"] += 1
        if await self.session.get(CourseProgram, (course.id, program.id)) is None:
            self.session.add(CourseProgram(course_id=course.id, program_id=program.id, semester_number=1))
        cs = await self.session.get(ClassSubject, (program.id, course.id))
        if cs is None:
            self.session.add(ClassSubject(program_id=program.id, course_id=course.id, sort_order=order, is_optional=optional))
        await self.session.flush()
        return course

    async def _ensure_section(self, term: AcademicTerm, program: Program, label: str, made: dict, actor_id) -> SchoolSection:
        sec = (await self.session.execute(select(SchoolSection).where(SchoolSection.term_id == term.id, SchoolSection.program_id == program.id,
                                                                      func.upper(SchoolSection.label) == label.upper()))).scalar_one_or_none()
        if sec is None:
            sec = SchoolSection(term_id=term.id, program_id=program.id, label=label)
            self.session.add(sec)
            await self.session.flush()
            made["sections"] += 1
        for subj in await self._subjects(program.id):
            await self._ensure_offering(term, sec, subj["course_id"], made)
        return sec

    async def _ensure_offering(self, term: AcademicTerm, sec: SchoolSection, course_id: uuid.UUID, made: dict | None = None) -> CourseOffering:
        off = (await self.session.execute(select(CourseOffering).where(CourseOffering.course_id == course_id, CourseOffering.term_label == term.label,
                                                                       CourseOffering.section_label == sec.label))).scalar_one_or_none()
        if off is None:
            off = CourseOffering(course_id=course_id, term_label=term.label, section_label=sec.label, capacity=None,
                                 term_start_date=term.start_date, term_end_date=term.end_date)
            self.session.add(off)
            await self.session.flush()
            if made is not None:
                made["subject_classes"] += 1
        return off

    # ------------------------------------------------------------------ reading
    async def _subjects(self, program_id: uuid.UUID) -> list[dict]:
        rows = (await self.session.execute(select(ClassSubject, Course).join(Course, Course.id == ClassSubject.course_id)
                                           .where(ClassSubject.program_id == program_id).order_by(ClassSubject.sort_order, Course.title))).all()
        return [{"course_id": c.id, "code": c.code, "title": c.title, "optional": cs.is_optional} for cs, c in rows]

    async def _pupils(self, program_id: uuid.UUID, label: str | None = None):
        q = select(Student).where(Student.program_id == program_id, Student.status == StudentStatus.ACTIVE)
        if label is not None:
            q = q.where(func.upper(Student.section_label) == label.upper())
        return list((await self.session.execute(q.order_by(Student.roll_number))).scalars())

    async def overview(self, term_label: str | None) -> dict:
        terms = [{"label": t.label, "status": t.status.value, "start_date": t.start_date, "end_date": t.end_date}
                 for t in await AcademicTermService(self.session).all()]
        try:
            term = await self.term(term_label)
        except ResourceNotFoundError:
            return {"school_mode": school_mode(), "session": None, "sessions": terms, "classes": [], "catalogue": catalogue.summary()}
        classes = list((await self.session.execute(select(Program).where(Program.school_level.is_not(None))
                                                   .order_by(Program.school_level, Program.name))).scalars())
        secs = list((await self.session.execute(select(SchoolSection).where(SchoolSection.term_id == term.id).order_by(SchoolSection.label))).scalars())
        teacher_ids = {s.class_teacher_id for s in secs if s.class_teacher_id}
        teachers = {e.id: e for e in (await self.session.execute(select(Employee).where(Employee.id.in_(teacher_ids)))).scalars()} if teacher_ids else {}
        counts = dict((await self.session.execute(select(func.concat(Student.program_id, "|", func.upper(Student.section_label)), func.count())
                                                  .where(Student.status == StudentStatus.ACTIVE, Student.program_id.in_([c.id for c in classes]))
                                                  .group_by(Student.program_id, func.upper(Student.section_label)))).all()) if classes else {}
        unplaced = dict((await self.session.execute(select(Student.program_id, func.count()).where(
            Student.status == StudentStatus.ACTIVE, Student.program_id.in_([c.id for c in classes]),
            or_(Student.section_label.is_(None), Student.section_label == "")).group_by(Student.program_id))).all()) if classes else {}
        names = {c.id: c.name for c in classes}
        out = []
        for c in classes:
            mine = [s for s in secs if s.program_id == c.id]
            subjects = await self._subjects(c.id)
            out.append({"id": c.id, "code": c.code, "name": c.name, "stage": c.school_stage, "stream": c.stream, "level": c.school_level,
                        "next_class": names.get(c.promotes_to_id), "subjects": subjects, "without_section": unplaced.get(c.id, 0),
                        "sections": [{"id": s.id, "label": s.label, "room": s.room, "capacity": s.capacity, "class_teacher_id": s.class_teacher_id,
                                      "class_teacher": _name(teachers.get(s.class_teacher_id)),
                                      "students": counts.get(f"{c.id}|{s.label.upper()}", 0)} for s in mine]})
        stage_label = dict(catalogue.STAGES)
        return {"school_mode": school_mode(), "session": {"id": term.id, "label": term.label, "start_date": term.start_date, "end_date": term.end_date},
                "sessions": terms, "stages": [{"key": k, "label": v} for k, v in catalogue.STAGES],
                "classes": [dict(x, stage_label=stage_label.get(x["stage"], "")) for x in out], "catalogue": catalogue.summary()}

    async def teachers(self) -> list[dict]:
        ids = select(UserRole.user_id).join(Role, Role.id == UserRole.role_id).where(Role.code.in_((Roles.TEACHER, Roles.HOD, Roles.PRINCIPAL)))
        rows = (await self.session.execute(select(Employee).join(User, User.id == Employee.user_id).where(
            Employee.status == EmployeeStatus.ACTIVE, Employee.user_id.in_(ids)).order_by(Employee.first_name, Employee.last_name))).scalars()
        return [{"id": e.id, "name": _name(e), "employee_code": e.employee_code} for e in rows]

    async def section_detail(self, section_id: uuid.UUID) -> dict:
        sec, program, term = await self._section(section_id)
        subjects = await self._subjects(program.id)
        offs = {o.course_id: o for o in (await self.session.execute(select(CourseOffering).where(
            CourseOffering.term_label == term.label, CourseOffering.section_label == sec.label,
            CourseOffering.course_id.in_([s["course_id"] for s in subjects])))).scalars()} if subjects else {}
        emp_ids = {o.assigned_teacher_id for o in offs.values() if o.assigned_teacher_id} | ({sec.class_teacher_id} if sec.class_teacher_id else set())
        emps = {e.id: e for e in (await self.session.execute(select(Employee).where(Employee.id.in_(emp_ids)))).scalars()} if emp_ids else {}
        pupils = await self._pupils(program.id, sec.label)
        taking = {}
        if pupils and offs:
            for sid, oid in (await self.session.execute(select(Enrollment.student_id, Enrollment.course_offering_id).where(
                    Enrollment.student_id.in_([p.id for p in pupils]), Enrollment.status == EnrollmentStatus.ENROLLED,
                    Enrollment.course_offering_id.in_([o.id for o in offs.values()])))).all():
                taking.setdefault(sid, set()).add(oid)
        by_off = {o.id: cid for cid, o in offs.items()}
        return {"id": sec.id, "label": sec.label, "room": sec.room, "capacity": sec.capacity, "session": term.label,
                "class": {"id": program.id, "name": program.name, "code": program.code},
                "class_teacher_id": sec.class_teacher_id, "class_teacher": _name(emps.get(sec.class_teacher_id)),
                "subjects": [{**s, "offering_id": offs[s["course_id"]].id if s["course_id"] in offs else None,
                              "teacher_id": offs[s["course_id"]].assigned_teacher_id if s["course_id"] in offs else None,
                              "teacher": _name(emps.get(offs[s["course_id"]].assigned_teacher_id)) if s["course_id"] in offs else None,
                              "students": sum(1 for p in pupils if s["course_id"] in offs and offs[s["course_id"]].id in taking.get(p.id, set()))}
                             for s in subjects],
                "students": [{"id": p.id, "name": f"{p.first_name} {p.last_name}".strip(), "roll_number": p.roll_number,
                              "subjects": sorted(str(by_off[o]) for o in taking.get(p.id, set()))} for p in pupils]}

    # ------------------------------------------------------------------ changing sections, subjects, teachers
    async def add_section(self, program_id: uuid.UUID, *, term_label: str | None, label: str, actor_id: uuid.UUID) -> dict:
        self.require_school_mode()
        program = await self._class(program_id)
        term = await self.term(term_label)
        label = _label(label).upper()
        if not label or len(label) > 30:
            raise ValidationAppError("Give the section a short name, e.g. B or Blue.")
        if (await self.session.execute(select(SchoolSection.id).where(SchoolSection.term_id == term.id, SchoolSection.program_id == program.id,
                                                                     func.upper(SchoolSection.label) == label))).first():
            raise DuplicateResourceError(f"{program.name} already has section {label} in {term.label}.")
        sec = await self._ensure_section(term, program, label, {"sections": 0, "subject_classes": 0}, actor_id)
        await self.audit.log(actor_user_id=actor_id, action="school.section_added", entity_type="school_section", entity_id=sec.id,
                             after={"class": program.name, "section": label, "session": term.label})
        return await self.section_detail(sec.id)

    async def update_section(self, section_id: uuid.UUID, *, changes: dict, actor_id: uuid.UUID) -> dict:
        sec, program, term = await self._section(section_id)
        before = {"class_teacher_id": str(sec.class_teacher_id) if sec.class_teacher_id else None, "room": sec.room, "capacity": sec.capacity}
        if "class_teacher_id" in changes:
            tid = changes["class_teacher_id"]
            if tid is not None:
                e = await self.session.get(Employee, tid)
                if e is None or e.status != EmployeeStatus.ACTIVE:
                    raise ValidationAppError("Choose an active teacher.")
            sec.class_teacher_id = tid
        if "room" in changes:
            sec.room = _label(changes["room"] or "") or None
        if "capacity" in changes:
            cap = changes["capacity"]
            if cap is not None and cap <= 0:
                raise ValidationAppError("Seats must be more than 0 (or left empty).")
            sec.capacity = cap
        await self.session.flush()
        after = {"class_teacher_id": str(sec.class_teacher_id) if sec.class_teacher_id else None, "room": sec.room, "capacity": sec.capacity}
        if before != after:
            await self.audit.log(actor_user_id=actor_id, action="school.section_changed", entity_type="school_section", entity_id=sec.id,
                                 before=before, after={**after, "class": program.name, "section": sec.label})
            if after["class_teacher_id"] and after["class_teacher_id"] != before["class_teacher_id"]:
                await self._tell_teacher(sec.class_teacher_id, f"You are class teacher of {program.name} - {sec.label}",
                                         f"You are now the class teacher of {program.name}, section {sec.label}, for {term.label}.")
        return await self.section_detail(sec.id)

    async def remove_section(self, section_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        sec, program, term = await self._section(section_id)
        if await self._pupils(program.id, sec.label):
            raise BusinessRuleViolationError("Move the students of this section to another section first.")
        offs = list((await self.session.execute(select(CourseOffering).join(ClassSubject, (ClassSubject.course_id == CourseOffering.course_id)
                                                                          & (ClassSubject.program_id == program.id))
                                                .where(CourseOffering.term_label == term.label, CourseOffering.section_label == sec.label))).scalars())
        for o in offs:
            if await self._has_history(o.id):
                raise BusinessRuleViolationError("This section already has classes or enrolments on record, so it is kept.")
        for o in offs:
            await self.session.delete(o)
        await self.session.delete(sec)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="school.section_removed", entity_type="school_section", entity_id=section_id,
                             before={"class": program.name, "section": sec.label, "session": term.label})

    async def _has_history(self, offering_id: uuid.UUID) -> bool:
        if (await self.session.execute(select(Enrollment.id).where(Enrollment.course_offering_id == offering_id).limit(1))).first():
            return True
        return bool((await self.session.execute(select(ClassSession.id).where(ClassSession.course_offering_id == offering_id).limit(1))).first())

    async def add_subject(self, program_id: uuid.UUID, *, title: str, code: str | None, optional: bool, term_label: str | None,
                          actor_id: uuid.UUID) -> dict:
        self.require_school_mode()
        program = await self._class(program_id)
        title = _label(title)
        if len(title) < 2:
            raise ValidationAppError("Give the subject's name.")
        short = re.sub(r"[^A-Z0-9]", "", (code or "").upper()) or re.sub(r"[^A-Z0-9]", "", title.upper())[:5]
        if not short:
            raise ValidationAppError("Give a short code for the subject, e.g. ARB.")
        existing = await self._subjects(program.id)
        if any(s["title"].lower() == title.lower() or s["code"] == f"{program.code}-{short}"[:20] for s in existing):
            raise DuplicateResourceError(f"{program.name} already has this subject.")
        course = await self._ensure_subject(program, short, title, len(existing), optional, {"subjects": 0}, actor_id)
        term = await self.term(term_label)
        for sec in (await self.session.execute(select(SchoolSection).where(SchoolSection.term_id == term.id, SchoolSection.program_id == program.id))).scalars():
            await self._ensure_offering(term, sec, course.id)
        await self.audit.log(actor_user_id=actor_id, action="school.subject_added", entity_type="program", entity_id=program.id,
                             after={"subject": title, "code": course.code, "optional": optional})
        return {"course_id": course.id, "code": course.code, "title": course.title, "optional": optional}

    async def remove_subject(self, program_id: uuid.UUID, course_id: uuid.UUID, *, term_label: str | None, actor_id: uuid.UUID) -> None:
        """Take a subject off a class. Refused once anyone is enrolled in it or classes were held this session."""
        program = await self._class(program_id)
        cs = await self.session.get(ClassSubject, (program.id, course_id))
        if cs is None:
            raise ResourceNotFoundError("This class does not have that subject.")
        term = await self.term(term_label)
        offs = list((await self.session.execute(select(CourseOffering).where(CourseOffering.course_id == course_id,
                                                                             CourseOffering.term_label == term.label))).scalars())
        for o in offs:
            if await self._has_history(o.id):
                raise BusinessRuleViolationError("Students are already enrolled in this subject (or classes were held), so it cannot be removed. "
                                                 "Withdraw them first.")
        for o in offs:
            await self.session.delete(o)
        await self.session.delete(cs)
        link = await self.session.get(CourseProgram, (course_id, program.id))
        if link is not None:
            await self.session.delete(link)
        await self.session.flush()
        course = await self.session.get(Course, course_id)
        await self.audit.log(actor_user_id=actor_id, action="school.subject_removed", entity_type="program", entity_id=program.id,
                             before={"subject": course.title if course else str(course_id)})

    async def set_subject_teachers(self, section_id: uuid.UUID, teachers: dict[uuid.UUID, uuid.UUID | None], *, actor_id: uuid.UUID) -> dict:
        sec, program, term = await self._section(section_id)
        subjects = {s["course_id"]: s for s in await self._subjects(program.id)}
        changed = []
        for course_id, tid in teachers.items():
            if course_id not in subjects:
                raise ValidationAppError("That subject is not taught in this class.")
            if tid is not None:
                e = await self.session.get(Employee, tid)
                if e is None or e.status != EmployeeStatus.ACTIVE:
                    raise ValidationAppError("Choose active teachers only.")
            off = await self._ensure_offering(term, sec, course_id)
            if off.assigned_teacher_id != tid:
                off.assigned_teacher_id = tid
                changed.append((subjects[course_id]["title"], tid))
        await self.session.flush()
        if changed:
            await self.audit.log(actor_user_id=actor_id, action="school.subject_teachers", entity_type="school_section", entity_id=sec.id,
                                 after={"class": program.name, "section": sec.label, "subjects": {t: str(i) if i else None for t, i in changed}})
            mine: dict[uuid.UUID, list[str]] = {}
            for title, tid in changed:
                if tid:
                    mine.setdefault(tid, []).append(title)
            for tid, titles in mine.items():
                await self._tell_teacher(tid, f"New class: {program.name} - {sec.label}",
                                         f"You now teach {', '.join(titles)} to {program.name}, section {sec.label} ({term.label}).")
        return await self.section_detail(sec.id)

    async def _tell_teacher(self, employee_id: uuid.UUID, title: str, message: str) -> None:
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification

        e = await self.session.get(Employee, employee_id)
        if e is not None and e.user_id:
            await create_notification(self.session, user_id=e.user_id, category=NotificationCategory.GENERAL, title=title, message=message,
                                      link="/dashboard/faculty/courses")

    # ------------------------------------------------------------------ students
    async def candidates(self, section_id: uuid.UUID, q: str | None) -> list[dict]:
        """Students who can be put in this section: active, and not already in it."""
        sec, program, _term = await self._section(section_id)
        stmt = select(Student, Program.name).join(Program, Program.id == Student.program_id, isouter=True).where(
            Student.status == StudentStatus.ACTIVE,
            or_(Student.program_id != program.id, Student.program_id.is_(None), Student.section_label.is_(None),
                func.upper(Student.section_label) != sec.label.upper()))
        if q and q.strip():
            like = f"%{q.strip().lower()}%"
            stmt = stmt.where(or_(func.lower(Student.first_name + " " + Student.last_name).like(like), func.lower(Student.roll_number).like(like)))
        else:
            stmt = stmt.where(or_(Student.program_id == program.id, Student.program_id.is_(None)))       # same class or no class yet
        rows = (await self.session.execute(stmt.order_by(Student.first_name, Student.last_name).limit(60))).all()
        return [{"id": s.id, "name": f"{s.first_name} {s.last_name}".strip(), "roll_number": s.roll_number, "class": cname,
                 "section": s.section_label} for s, cname in rows]

    async def place(self, section_id: uuid.UUID, student_ids: list[uuid.UUID], *, optional_course_ids: list[uuid.UUID],
                    actor_id: uuid.UUID) -> dict:
        """Put students in this section and enrol them in its subjects (all compulsory ones, and the optional ones ticked).
        A student moving from another section of the same session keeps nothing behind: their subjects move with them,
        unless classes or marks are already on record there (then the move is refused with the reason)."""
        from app.academics.service import EnrollmentService

        self.require_school_mode()
        sec, program, term = await self._section(section_id)
        subjects = await self._subjects(program.id)
        optional = {s["course_id"] for s in subjects if s["optional"]}
        bad = [c for c in optional_course_ids if c not in optional]
        if bad:
            raise ValidationAppError("Choose optional subjects of this class only.")
        wanted = [s["course_id"] for s in subjects if not s["optional"] or s["course_id"] in set(optional_course_ids)]
        offs = {cid: await self._ensure_offering(term, sec, cid) for cid in wanted}
        if sec.capacity is not None:
            now = len(await self._pupils(program.id, sec.label))
            new = 0
            for sid in set(student_ids):
                new += 0 if await self._in_section(sid, program.id, sec.label) else 1
            if now + new > sec.capacity:
                raise BusinessRuleViolationError(f"Section {sec.label} has {sec.capacity} seats; {now} are taken.")
        placed, failed = [], []
        enroll = EnrollmentService(self.session)
        self.actor_id = actor_id
        for sid in dict.fromkeys(student_ids):
            s = await self.session.get(Student, sid)
            if s is None or s.status != StudentStatus.ACTIVE:
                failed.append({"student_id": sid, "reason": "Not an active student."})
                continue
            try:
                async with self.session.begin_nested():
                    moved = await self._release_other_sections(s, term, program, sec)
                    prev = (s.program_id, s.current_semester)
                    s.program_id, s.current_semester, s.section_label = program.id, 1, sec.label
                    await self.session.flush()
                    if prev != (s.program_id, s.current_semester):
                        from app.fees.service import FeeStructureService

                        await FeeStructureService(self.session).assign_matching_active_structures_to_student(s, actor_id=actor_id)
                    have = set((await self.session.execute(select(Enrollment.course_offering_id).where(
                        Enrollment.student_id == s.id, Enrollment.status == EnrollmentStatus.ENROLLED))).scalars())
                    count = 0
                    for cid, off in offs.items():
                        if off.id in have:
                            continue
                        if cid in moved:                                  # the same subject from the old section: move the row
                            moved[cid].course_offering_id = off.id
                        else:
                            await enroll.enroll(student_id=s.id, course_offering_id=off.id, actor_id=actor_id, by_admin=True)
                        count += 1
                    placed.append({"student_id": s.id, "name": f"{s.first_name} {s.last_name}".strip(), "subjects": count})
            except AppError as e:
                failed.append({"student_id": sid, "name": f"{s.first_name} {s.last_name}".strip(), "reason": e.message})
        if placed:
            await self.audit.log(actor_user_id=actor_id, action="school.students_placed", entity_type="school_section", entity_id=sec.id,
                                 after={"class": program.name, "section": sec.label, "students": len(placed)})
        return {"placed": placed, "failed": failed, "section": await self.section_detail(sec.id)}

    async def _in_section(self, student_id, program_id, label) -> bool:
        s = await self.session.get(Student, student_id)
        return bool(s and s.program_id == program_id and (s.section_label or "").upper() == label.upper())

    async def _release_other_sections(self, s: Student, term: AcademicTerm, program: Program, sec: SchoolSection) -> dict:
        """The student's current enrolments in this session's school subjects of another section or class.
        Returns {course_id: enrolment} for the subjects they keep (same class, other section), so the row can move.
        Refuses when any of them already has attendance or marks; withdraws the rest (reason CLASS_CHANGE)."""
        from app.academics.models import StudentMark
        from app.academics.service import EnrollmentService
        from app.attendance.models import AttendanceRecord

        rows = (await self.session.execute(select(Enrollment, CourseOffering).join(CourseOffering, CourseOffering.id == Enrollment.course_offering_id)
                                           .join(ClassSubject, ClassSubject.course_id == CourseOffering.course_id)
                                           .where(Enrollment.student_id == s.id, Enrollment.status == EnrollmentStatus.ENROLLED,
                                                  CourseOffering.term_label == term.label))).all()
        keep: dict[uuid.UUID, Enrollment] = {}
        for e, off in rows:
            if off.section_label.upper() == sec.label.upper() and (s.program_id == program.id):
                continue
            used = (await self.session.execute(select(AttendanceRecord.id).join(ClassSession, ClassSession.id == AttendanceRecord.class_session_id)
                                               .where(AttendanceRecord.student_id == s.id, ClassSession.course_offering_id == off.id).limit(1))).first() or \
                (await self.session.execute(select(StudentMark.id).where(StudentMark.enrollment_id == e.id).limit(1))).first()
            if used:
                raise BusinessRuleViolationError(f"{s.first_name} already has attendance or marks in section {off.section_label}; "
                                                 "a move after classes have started is not supported yet.")
            if s.program_id == program.id:
                keep[off.course_id] = e
            else:                                          # another class: kept on record as withdrawn, not shown on a transcript
                await EnrollmentService(self.session).withdraw(e.id, student_id=None, actor_id=self.actor_id, reason="CLASS_CHANGE",
                                                               keep_future_fees=False)
        await self.session.flush()
        return keep
