"""School mode, stage 2: the morning roll call, the homework diary and parent-teacher meetings (PTM).

Roll call
  The class teacher (or the cover teacher the admin sets for that day) marks each student of the section
  Present / Absent / Late / Leave once a day. Teachers mark today only; the office can mark or correct any past day.
  Parents of a child marked Absent or Late are told at once (app message to each parent login + SMS / WhatsApp to the
  guardian, Settings -> sms_on_school_absence); a correction from Absent to Present tells them too.
  For school classes this daily roll call is THE attendance: every subject's attendance % (admit cards, warnings,
  the parents' weekly note, report cards) comes from it (school_subject_percentage, used by
  AttendanceService.compute_percentage), and per-period subject attendance is not taken.

Homework diary
  A subject teacher writes homework for a section they teach (title, details, due date, optional PDF / picture).
  Students and parents see it in the portal; once a day in the evening (Settings -> homework_digest_hour) each
  family gets one message with all of that day's homework (sms_on_homework).

PTM
  The office sets a meeting day (date, hours, minutes per meeting). Every class teacher gets slots for that day, and
  parents book one slot per child with the child's class teacher in the parent portal. The teacher sees who comes when,
  and notes what was discussed.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import Course, CourseOffering
from app.academics.terms import AcademicTerm
from app.audit.service import AuditService
from app.auth.permissions import Permissions
from app.core.clock import institute_now, institute_today
from app.core.config import settings
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.core.logging import get_logger
from app.employees.models import Employee, EmployeeStatus
from app.programs.models import Program
from app.school.models import ClassSubject, DailyAttendance, DayStatus, Homework, PtmDay, PtmSlot, RollCallCover, SchoolSection
from app.school.service import SchoolService, school_mode
from app.students.models import Student, StudentStatus

logger = get_logger(__name__)
HOMEWORK_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
HOMEWORK_MAX_BYTES = 5 * 1024 * 1024


def _name(p) -> str:
    return f"{p.first_name} {p.last_name}".strip() if p else ""


def is_office(user) -> bool:
    return Permissions.ATTENDANCE_LOCK in user.permissions or Permissions.COURSE_MANAGE in user.permissions


async def employee_of(session: AsyncSession, user) -> Employee | None:
    return (await session.execute(select(Employee).where(Employee.user_id == user.id))).scalar_one_or_none()


async def current_section(session: AsyncSession, student: Student) -> SchoolSection | None:
    """The student's section in the current school session (their class + section letter)."""
    if not student.program_id or not student.section_label:
        return None
    try:
        term = await SchoolService(session).term(None)
    except ResourceNotFoundError:
        return None
    return (await session.execute(select(SchoolSection).where(SchoolSection.term_id == term.id, SchoolSection.program_id == student.program_id,
                                                              func.upper(SchoolSection.label) == student.section_label.upper()))).scalar_one_or_none()


async def can_see_student(session: AsyncSession, user, student_id: uuid.UUID) -> Student:
    """The student themself, a linked parent, a teacher of the student's section, or the office."""
    from app.parents.models import ParentStudentLink

    s = await session.get(Student, student_id)
    if s is None:
        raise ResourceNotFoundError("Student not found.")
    if s.user_id == user.id or is_office(user):
        return s
    if (await session.execute(select(ParentStudentLink.user_id).where(ParentStudentLink.user_id == user.id,
                                                                 ParentStudentLink.student_id == student_id))).first():
        return s
    emp = await employee_of(session, user)
    sec = await current_section(session, s)
    if emp and sec and (sec.class_teacher_id == emp.id or await _teaches_section(session, emp.id, sec)):
        return s
    raise AuthorizationError("You cannot see this student.")


async def _teaches_section(session: AsyncSession, employee_id: uuid.UUID, sec: SchoolSection) -> bool:
    term = await session.get(AcademicTerm, sec.term_id)
    return bool((await session.execute(select(CourseOffering.id).join(ClassSubject, (ClassSubject.course_id == CourseOffering.course_id)
                                                                      & (ClassSubject.program_id == sec.program_id))
                                       .where(CourseOffering.term_label == term.label, CourseOffering.section_label == sec.label,
                                              CourseOffering.assigned_teacher_id == employee_id).limit(1))).first())


async def section_of_offering(session: AsyncSession, off: CourseOffering) -> SchoolSection | None:
    cs = (await session.execute(select(ClassSubject).where(ClassSubject.course_id == off.course_id))).scalars().first()
    if cs is None:
        return None
    return (await session.execute(select(SchoolSection).join(AcademicTerm, AcademicTerm.id == SchoolSection.term_id).where(
        func.lower(AcademicTerm.label) == off.term_label.lower(), SchoolSection.program_id == cs.program_id,
        func.upper(SchoolSection.label) == off.section_label.upper()))).scalar_one_or_none()


async def is_school_subject(session: AsyncSession, offering_id: uuid.UUID) -> bool:
    if not school_mode():
        return False
    off = await session.get(CourseOffering, offering_id)
    return bool(off and (await session.execute(select(ClassSubject.program_id).where(ClassSubject.course_id == off.course_id).limit(1))).first())


async def school_subject_percentage(session: AsyncSession, student_id: uuid.UUID, offering_id: uuid.UUID) -> dict | None:
    """In School mode, a school subject's attendance is the student's daily roll call over the subject's term.
    None for anything else (then the usual per-class attendance applies)."""
    if not await is_school_subject(session, offering_id):
        return None
    off = await session.get(CourseOffering, offering_id)
    q = select(DailyAttendance.status, func.count()).where(DailyAttendance.student_id == student_id)
    if off.term_start_date:
        q = q.where(DailyAttendance.day >= off.term_start_date)
    if off.term_end_date:
        q = q.where(DailyAttendance.day <= off.term_end_date)
    counts = dict((await session.execute(q.group_by(DailyAttendance.status))).all())
    present = counts.get(DayStatus.PRESENT, 0) + counts.get(DayStatus.LATE, 0)
    leave = counts.get(DayStatus.LEAVE, 0)
    total = present + counts.get(DayStatus.ABSENT, 0)
    return {"total_sessions_taken": total, "present_count": present, "percentage": 100.0 if total == 0 else round(present / total * 100, 2),
            "absent_count": total - present, "leave_count": leave}


# ====================================================================== roll call
TEXT = {
    "ABSENT": ("{first} is absent from school today ({day}). If you did not know, please contact the class teacher.",
               "{first} آج ({day}) اسکول سے غیر حاضر ہیں۔ اگر آپ کو علم نہیں تو کلاس ٹیچر سے رابطہ کریں۔"),
    "LATE": ("{first} came to school late today ({day}).", "{first} آج ({day}) اسکول دیر سے آئے۔"),
    "CORRECTED": ("Correction: {first} is in school today ({day}).", "درستی: {first} آج ({day}) اسکول میں حاضر ہیں۔"),
}
TITLE = {"ABSENT": "Absent from school today", "LATE": "Late to school today", "CORRECTED": "Present in school today"}


class RollCallService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def _section(self, section_id: uuid.UUID) -> tuple[SchoolSection, Program, AcademicTerm]:
        return await SchoolService(self.session)._section(section_id)

    async def _who(self, sec: SchoolSection, day: date, user) -> str:
        """'office', 'class_teacher' or 'cover'; otherwise not allowed."""
        if is_office(user):
            return "office"
        emp = await employee_of(self.session, user)
        if emp is not None:
            if sec.class_teacher_id == emp.id:
                return "class_teacher"
            cover = (await self.session.execute(select(RollCallCover).where(RollCallCover.section_id == sec.id, RollCallCover.day == day))).scalar_one_or_none()
            if cover is not None and cover.employee_id == emp.id:
                return "cover"
        raise AuthorizationError("Only the class teacher (or the teacher covering today) can take this section's roll call.")

    async def mine(self, user) -> list[dict]:
        """Sections whose roll call this person takes today: as class teacher, or covering."""
        emp = await employee_of(self.session, user)
        if emp is None:
            return []
        try:
            term = await SchoolService(self.session).term(None)
        except ResourceNotFoundError:
            return []
        today = institute_today()
        covered = set((await self.session.execute(select(RollCallCover.section_id).where(RollCallCover.employee_id == emp.id,
                                                                                         RollCallCover.day == today))).scalars())
        rows = (await self.session.execute(select(SchoolSection, Program).join(Program, Program.id == SchoolSection.program_id).where(
            SchoolSection.term_id == term.id, (SchoolSection.class_teacher_id == emp.id) | SchoolSection.id.in_(covered or {uuid.uuid4()}))
            .order_by(Program.school_level, SchoolSection.label))).all()
        return [{"id": s.id, "label": s.label, "class": p.name, "session": term.label, "covering": s.id in covered and s.class_teacher_id != emp.id}
                for s, p in rows]

    async def sheet(self, section_id: uuid.UUID, day: date, user) -> dict:
        sec, program, term = await self._section(section_id)
        who = await self._who(sec, day, user)
        pupils = await SchoolService(self.session)._pupils(program.id, sec.label)
        marks = {m.student_id: m for m in (await self.session.execute(select(DailyAttendance).where(
            DailyAttendance.section_id == sec.id, DailyAttendance.day == day))).scalars()}
        today = institute_today()
        can_mark = day <= today if who == "office" else day == today
        rows = [{"student_id": p.id, "name": _name(p), "roll_number": p.roll_number,
                 "status": marks[p.id].status.value if p.id in marks else None, "note": marks[p.id].note if p.id in marks else None}
                for p in pupils]
        counts = {k.value: sum(1 for r in rows if r["status"] == k.value) for k in DayStatus}
        cover = (await self.session.execute(select(RollCallCover, Employee).join(Employee, Employee.id == RollCallCover.employee_id).where(
            RollCallCover.section_id == sec.id, RollCallCover.day == day))).first()
        return {"section": {"id": sec.id, "label": sec.label, "class": program.name, "session": term.label}, "day": day, "is_today": day == today,
                "can_mark": can_mark, "you_are": who, "students": rows, "counts": {**counts, "unmarked": sum(1 for r in rows if r["status"] is None)},
                "cover": _name(cover[1]) if cover else None}

    async def mark(self, section_id: uuid.UUID, day: date, entries: list[dict], user) -> dict:
        if not school_mode():
            raise BusinessRuleViolationError("School mode is off.")
        sec, program, term = await self._section(section_id)
        who = await self._who(sec, day, user)
        today = institute_today()
        if day > today:
            raise BusinessRuleViolationError("The roll call cannot be taken for a day that has not come yet.")
        if who != "office" and day != today:
            raise BusinessRuleViolationError("Teachers take today's roll call only. Ask the office to correct an earlier day.")
        pupils = {p.id: p for p in await SchoolService(self.session)._pupils(program.id, sec.label)}
        current = {m.student_id: m for m in (await self.session.execute(select(DailyAttendance).where(
            DailyAttendance.student_id.in_(list(pupils) or [uuid.uuid4()]), DailyAttendance.day == day))).scalars()}
        tell: list[tuple[uuid.UUID, str]] = []
        changed = 0
        now = datetime.now(timezone.utc)
        for e in entries:
            sid, status = e["student_id"], DayStatus(e["status"])
            if sid not in pupils:
                raise ValidationAppError("A student on the list is not in this section.")
            note = (" ".join((e.get("note") or "").split()) or None)
            m = current.get(sid)
            if m is None:
                m = DailyAttendance(section_id=sec.id, student_id=sid, day=day, status=status, note=note, marked_by=user.id)
                self.session.add(m)
                current[sid] = m
                changed += 1
            elif m.status != status or (m.note or None) != note:
                m.status, m.note, m.marked_by, m.marked_at, m.section_id = status, note, user.id, now, sec.id
                changed += 1
            if day == today:
                if status in (DayStatus.ABSENT, DayStatus.LATE) and m.told_status != status.value:
                    tell.append((sid, status.value))
                    m.told_status = status.value
                elif status == DayStatus.PRESENT and m.told_status == "ABSENT":
                    tell.append((sid, "CORRECTED"))
                    m.told_status = None
        await self.session.flush()
        if changed:
            await self.audit.log(actor_user_id=user.id, action="school.roll_call", entity_type="school_section", entity_id=sec.id,
                                 after={"class": program.name, "section": sec.label, "day": day.isoformat(), "changed": changed,
                                        "absent": sum(1 for m in current.values() if m.status == DayStatus.ABSENT)})
        for sid, kind in tell:
            await self._tell_family(pupils[sid], kind, day)
        return await self.sheet(sec.id, day, user)

    async def _tell_family(self, s: Student, kind: str, day: date) -> None:
        from app.notifications.family import _notify, family

        try:
            async with self.session.begin_nested():
                f = (await family(self.session, {s.id})).get(s.id)
                when = day.strftime("%d %b")
                for uid, lang in (f["parents"] if f else []):
                    msg = TEXT[kind][1 if lang == "ur" else 0].format(first=s.first_name, day=when)
                    await _notify(self.session, uid, TITLE[kind], msg, "/dashboard/parent/diary")
                lang = f["parents"][0][1] if f and f["parents"] else "en"
                from app.sms.service import SmsService

                await SmsService(self.session).queue_to_guardian(
                    s.id, event="SCHOOL_ABSENCE", body=f"{settings.app_name}: " + TEXT[kind][1 if lang == "ur" else 0].format(first=s.first_name, day=when))
        except Exception as exc:  # noqa: BLE001 - the roll call is saved; a message problem must not undo it
            logger.warning("roll_call_message_failed", error=str(exc)[:200])

    async def set_cover(self, section_id: uuid.UUID, day: date, employee_id: uuid.UUID | None, actor_id: uuid.UUID) -> dict:
        sec, program, term = await self._section(section_id)
        if day < institute_today():
            raise BusinessRuleViolationError("A cover teacher can be set for today or a later day.")
        cover = (await self.session.execute(select(RollCallCover).where(RollCallCover.section_id == sec.id, RollCallCover.day == day))).scalar_one_or_none()
        if employee_id is None:
            if cover is not None:
                await self.session.delete(cover)
        else:
            e = await self.session.get(Employee, employee_id)
            if e is None or e.status != EmployeeStatus.ACTIVE:
                raise ValidationAppError("Choose an active teacher.")
            if cover is None:
                self.session.add(RollCallCover(section_id=sec.id, day=day, employee_id=employee_id, set_by=actor_id))
            else:
                cover.employee_id, cover.set_by = employee_id, actor_id
            await SchoolService(self.session)._tell_teacher(employee_id, f"Roll call: {program.name} - {sec.label} on {day:%d %b}",
                                                            f"Please take the roll call of {program.name}, section {sec.label}, on {day:%a %d %b} "
                                                            "(the class teacher is away). Open My class.")
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="school.roll_call_cover", entity_type="school_section", entity_id=sec.id,
                             after={"day": day.isoformat(), "employee_id": str(employee_id) if employee_id else None})
        return {"section_id": sec.id, "day": day, "employee_id": employee_id}

    async def overview(self, day: date, term_label: str | None) -> dict:
        """The office's view of one day: every section, whether its roll call is taken, and who is absent."""
        term = await SchoolService(self.session).term(term_label)
        secs = (await self.session.execute(select(SchoolSection, Program).join(Program, Program.id == SchoolSection.program_id)
                                           .where(SchoolSection.term_id == term.id).order_by(Program.school_level, Program.name, SchoolSection.label))).all()
        size = dict((await self.session.execute(select(func.concat(Student.program_id, "|", func.upper(Student.section_label)), func.count())
                                                .where(Student.status == StudentStatus.ACTIVE).group_by(Student.program_id, func.upper(Student.section_label)))).all())
        marks = (await self.session.execute(select(DailyAttendance.section_id, DailyAttendance.status, func.count()).where(DailyAttendance.day == day)
                                            .group_by(DailyAttendance.section_id, DailyAttendance.status))).all()
        by_sec: dict = {}
        for sid, st, n in marks:
            by_sec.setdefault(sid, {})[st.value] = n
        covers = {c.section_id: c.employee_id for c in (await self.session.execute(select(RollCallCover).where(RollCallCover.day == day))).scalars()}
        emp_ids = {s.class_teacher_id for s, _ in secs if s.class_teacher_id} | set(covers.values())
        emps = {e.id: _name(e) for e in (await self.session.execute(select(Employee).where(Employee.id.in_(emp_ids or {uuid.uuid4()})))).scalars()}
        rows = []
        for s, p in secs:
            c = by_sec.get(s.id, {})
            marked = sum(c.values())
            rows.append({"id": s.id, "class": p.name, "label": s.label, "students": size.get(f"{p.id}|{s.label.upper()}", 0), "marked": marked,
                         "present": c.get("PRESENT", 0), "absent": c.get("ABSENT", 0), "late": c.get("LATE", 0), "leave": c.get("LEAVE", 0),
                         "class_teacher": emps.get(s.class_teacher_id), "cover": emps.get(covers.get(s.id)), "cover_id": covers.get(s.id)})
        total = sum(r["students"] for r in rows)
        return {"day": day, "session": term.label, "sections": rows,
                "totals": {"students": total, "absent": sum(r["absent"] for r in rows), "late": sum(r["late"] for r in rows),
                           "not_taken": sum(1 for r in rows if r["students"] and not r["marked"])}}

    async def month(self, student_id: uuid.UUID, year: int, month: int) -> dict:
        first = date(year, month, 1)
        last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        rows = (await self.session.execute(select(DailyAttendance).where(DailyAttendance.student_id == student_id, DailyAttendance.day >= first,
                                                                         DailyAttendance.day <= last).order_by(DailyAttendance.day))).scalars().all()
        counts = {k.value: sum(1 for r in rows if r.status == k) for k in DayStatus}
        taken = counts["PRESENT"] + counts["LATE"] + counts["ABSENT"]
        return {"month": first.strftime("%Y-%m"), "days": [{"day": r.day, "status": r.status.value, "note": r.note} for r in rows],
                "counts": counts, "percentage": None if not taken else round((counts["PRESENT"] + counts["LATE"]) / taken * 100, 1)}


# ====================================================================== homework
class HomeworkService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def my_classes(self, user) -> list[dict]:
        """The section-subjects this teacher teaches in the current session."""
        emp = await employee_of(self.session, user)
        if emp is None:
            return []
        try:
            term = await SchoolService(self.session).term(None)
        except ResourceNotFoundError:
            return []
        rows = (await self.session.execute(select(CourseOffering, Course, ClassSubject.program_id).join(Course, Course.id == CourseOffering.course_id)
                                           .join(ClassSubject, ClassSubject.course_id == CourseOffering.course_id)
                                           .where(CourseOffering.term_label == term.label, CourseOffering.assigned_teacher_id == emp.id))).all()
        out = []
        for off, course, _pid in rows:
            sec = await section_of_offering(self.session, off)
            if sec is None:
                continue
            program = await self.session.get(Program, sec.program_id)
            out.append({"offering_id": off.id, "section_id": sec.id, "class": program.name, "section": sec.label, "subject": course.title,
                        "course_id": course.id, "level": program.school_level})
        out.sort(key=lambda r: (r["level"] or 0, r["class"], r["section"], r["subject"]))
        return out

    async def post(self, user, *, offering_id: uuid.UUID, title: str, details: str | None, due_on: date | None,
                   file: tuple[bytes, str, str] | None) -> dict:
        if not school_mode():
            raise BusinessRuleViolationError("School mode is off.")
        off = await self.session.get(CourseOffering, offering_id)
        if off is None:
            raise ResourceNotFoundError("Class not found.")
        sec = await section_of_offering(self.session, off)
        if sec is None:
            raise BusinessRuleViolationError("This is not a school class.")
        emp = await employee_of(self.session, user)
        if not is_office(user) and (emp is None or off.assigned_teacher_id != emp.id):
            raise AuthorizationError("Only the subject's teacher can set its homework.")
        title = " ".join((title or "").split())
        if len(title) < 3:
            raise ValidationAppError("Write the homework, e.g. Exercise 4.2, questions 1-5.")
        today = institute_today()
        if due_on is not None and due_on < today:
            raise ValidationAppError("The due date has passed.")
        hw = Homework(section_id=sec.id, course_id=off.course_id, set_on=today, due_on=due_on, title=title[:150],
                      details=(details or "").strip()[:2000] or None, created_by=user.id)
        self.session.add(hw)
        await self.session.flush()
        if file is not None:
            from app.core.storage import FileTooLargeError, UnsupportedFileTypeError, save_uploaded_file

            content, name, ctype = file
            try:
                hw.file_key, hw.file_name = save_uploaded_file(category="homework", entity_id=hw.id, content=content, original_filename=name or "homework",
                                                               content_type=ctype or "", allowed_content_types=HOMEWORK_TYPES, max_bytes=HOMEWORK_MAX_BYTES)
            except (UnsupportedFileTypeError, FileTooLargeError) as exc:
                raise ValidationAppError(str(exc)) from exc
            hw.file_type = ctype
            await self.session.flush()
        course = await self.session.get(Course, off.course_id)
        await self.audit.log(actor_user_id=user.id, action="school.homework_set", entity_type="school_homework", entity_id=hw.id,
                             after={"section_id": str(sec.id), "subject": course.title, "title": hw.title})
        return await self._row(hw)

    async def _row(self, hw: Homework, *, with_class: bool = True) -> dict:
        course = await self.session.get(Course, hw.course_id)
        out = {"id": hw.id, "subject": course.title if course else "", "title": hw.title, "details": hw.details, "set_on": hw.set_on, "due_on": hw.due_on,
               "file_name": hw.file_name, "file_url": f"/api/v1/school/homework/{hw.id}/file" if hw.file_key else None}
        if with_class:
            sec = await self.session.get(SchoolSection, hw.section_id)
            program = await self.session.get(Program, sec.program_id) if sec else None
            out.update({"class": program.name if program else "", "section": sec.label if sec else "", "section_id": hw.section_id})
        return out

    async def remove(self, homework_id: uuid.UUID, user) -> None:
        hw = await self.session.get(Homework, homework_id)
        if hw is None:
            raise ResourceNotFoundError("Homework not found.")
        if not is_office(user):
            if hw.created_by != user.id:
                raise AuthorizationError("Only the teacher who set it can remove it.")
            if hw.set_on != institute_today():
                raise BusinessRuleViolationError("Homework can be removed on the day it was set. Ask the office after that.")
        from app.core.storage import delete_file

        delete_file(hw.file_key)
        await self.session.delete(hw)
        await self.session.flush()
        await self.audit.log(actor_user_id=user.id, action="school.homework_removed", entity_type="school_homework", entity_id=homework_id,
                             before={"title": hw.title, "set_on": hw.set_on.isoformat()})

    async def for_teacher(self, user, days: int = 14) -> dict:
        since = institute_today() - timedelta(days=days)
        rows = (await self.session.execute(select(Homework).where(Homework.created_by == user.id, Homework.set_on >= since)
                                           .order_by(Homework.set_on.desc(), Homework.created_at.desc()))).scalars().all()
        today = institute_today()
        return {"classes": await self.my_classes(user), "homework": [{**await self._row(h), "can_remove": h.set_on == today} for h in rows]}

    async def for_student(self, student_id: uuid.UUID, days: int = 14) -> dict:
        s = await self.session.get(Student, student_id)
        sec = await current_section(self.session, s) if s else None
        if sec is None:
            return {"section": None, "homework": []}
        since = institute_today() - timedelta(days=days)
        rows = (await self.session.execute(select(Homework).where(Homework.section_id == sec.id, Homework.set_on >= since)
                                           .order_by(Homework.set_on.desc(), Homework.created_at))).scalars().all()
        program = await self.session.get(Program, sec.program_id)
        return {"section": {"class": program.name, "label": sec.label}, "homework": [await self._row(h, with_class=False) for h in rows]}

    async def file(self, homework_id: uuid.UUID, user) -> Homework:
        hw = await self.session.get(Homework, homework_id)
        if hw is None or not hw.file_key:
            raise ResourceNotFoundError("File not found.")
        if is_office(user) or hw.created_by == user.id:
            return hw
        from app.parents.models import ParentStudentLink

        sec = await self.session.get(SchoolSection, hw.section_id)
        mine = (await self.session.execute(select(Student).where(Student.user_id == user.id))).scalar_one_or_none()
        kids = list((await self.session.execute(select(Student).join(ParentStudentLink, ParentStudentLink.student_id == Student.id)
                                                .where(ParentStudentLink.user_id == user.id))).scalars())
        for s in ([mine] if mine else []) + kids:
            if s.program_id == sec.program_id and (s.section_label or "").upper() == sec.label.upper():
                return hw
        emp = await employee_of(self.session, user)
        if emp and (sec.class_teacher_id == emp.id or await _teaches_section(self.session, emp.id, sec)):
            return hw
        raise AuthorizationError("You cannot open this file.")


async def homework_digest(session: AsyncSession) -> int:
    """Once a day from Settings -> homework_digest_hour: each family gets ONE message with all of today's homework.
    Never raises. Returns how many families were told."""
    from app.notifications.family import _notify, _once_a_day, family

    try:
        if not school_mode():
            return 0
        day = await _once_a_day(session, "homework-digest", hour=int(getattr(settings, "homework_digest_hour", 18)))
        if day is None:
            return 0
        from app.core.background import beat

        today = date.fromisoformat(day)
        rows = (await session.execute(select(Homework, Course.title).join(Course, Course.id == Homework.course_id).where(Homework.set_on == today)
                                      .order_by(Course.title))).all()
        per_section: dict = {}
        for hw, subject in rows:
            per_section.setdefault(hw.section_id, []).append(f"{subject}: {hw.title}" + (f" (by {hw.due_on:%d %b})" if hw.due_on else ""))
        told = 0
        for sec_id, items in per_section.items():
            sec = await session.get(SchoolSection, sec_id)
            pupils = await SchoolService(session)._pupils(sec.program_id, sec.label)
            fam = await family(session, {p.id for p in pupils})
            for p in pupils:
                text = "; ".join(items)
                f = fam.get(p.id)
                for uid, _lang in (f["parents"] if f else []):
                    await _notify(session, uid, f"Homework for {p.first_name}, {today:%d %b}", text[:500], "/dashboard/parent/diary")
                try:
                    from app.sms.service import SmsService

                    async with session.begin_nested():
                        await SmsService(session).queue_to_guardian(p.id, event="SCHOOL_HOMEWORK",
                                                                   body=f"{settings.app_name}: Homework for {p.first_name} ({today:%d %b}): {text}")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("homework_sms_failed", error=str(exc)[:200])
                told += 1
        await session.flush()
        await beat(session, "homework-digest", f"{day} told {told}")
        return told
    except Exception as exc:  # noqa: BLE001
        logger.warning("homework_digest_failed", error=str(exc)[:300])
        return 0


# ====================================================================== parent-teacher meetings
class PtmService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def create(self, *, term_label: str | None, day: date, start: time, end: time, slot_minutes: int, note: str | None, actor_id: uuid.UUID) -> dict:
        if not school_mode():
            raise BusinessRuleViolationError("School mode is off.")
        term = await SchoolService(self.session).term(term_label)
        if day < institute_today():
            raise ValidationAppError("Choose a day that has not passed.")
        if not 5 <= slot_minutes <= 60:
            raise ValidationAppError("Each meeting can be 5 to 60 minutes.")
        if end <= start:
            raise ValidationAppError("The end time must be after the start time.")
        teachers = sorted({s.class_teacher_id for s in (await self.session.execute(select(SchoolSection).where(
            SchoolSection.term_id == term.id, SchoolSection.class_teacher_id.is_not(None)))).scalars()}, key=str)
        if not teachers:
            raise BusinessRuleViolationError("No section has a class teacher yet. Choose class teachers in Classes & sections first.")
        ptm = PtmDay(term_id=term.id, day=day, start_time=start, end_time=end, slot_minutes=slot_minutes, note=(note or "").strip()[:300] or None,
                     created_by=actor_id)
        self.session.add(ptm)
        await self.session.flush()
        n = 0
        for t in teachers:
            cur = datetime.combine(day, start)
            while cur + timedelta(minutes=slot_minutes) <= datetime.combine(day, end):
                self.session.add(PtmSlot(ptm_day_id=ptm.id, employee_id=t, start_time=cur.time(), end_time=(cur + timedelta(minutes=slot_minutes)).time()))
                cur += timedelta(minutes=slot_minutes)
                n += 1
        if n == 0:
            raise ValidationAppError("The hours are shorter than one meeting.")
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="school.ptm_created", entity_type="school_ptm_day", entity_id=ptm.id,
                             after={"day": day.isoformat(), "slots": n, "teachers": len(teachers)})
        await self._announce(ptm, term)
        return await self.get(ptm.id)

    async def _announce(self, ptm: PtmDay, term: AcademicTerm) -> None:
        from app.notifications.family import _notify, family

        try:
            async with self.session.begin_nested():
                for t in {s.employee_id for s in (await self.session.execute(select(PtmSlot).where(PtmSlot.ptm_day_id == ptm.id))).scalars()}:
                    await SchoolService(self.session)._tell_teacher(t, f"Parent-teacher meeting on {ptm.day:%d %b}",
                                                                    f"Parents will book times with you on {ptm.day:%a %d %b}, {ptm.start_time:%H:%M}-"
                                                                    f"{ptm.end_time:%H:%M}. See My class.")
                secs = (await self.session.execute(select(SchoolSection).where(SchoolSection.term_id == term.id,
                                                                               SchoolSection.class_teacher_id.is_not(None)))).scalars().all()
                pupils = [p for s in secs for p in await SchoolService(self.session)._pupils(s.program_id, s.label)]
                fam = await family(self.session, {p.id for p in pupils})
                text = (f"Parent-teacher meeting on {ptm.day:%a %d %b}, {ptm.start_time:%H:%M}-{ptm.end_time:%H:%M}. "
                        "Please book a time with the class teacher in the parent portal (Meetings).")
                from app.sms.service import SmsService

                for p in pupils:
                    for uid, _l in (fam.get(p.id) or {"parents": []})["parents"]:
                        await _notify(self.session, uid, "Parent-teacher meeting", text, "/dashboard/parent/meetings")
                    await SmsService(self.session).queue_to_guardian(p.id, event="SCHOOL_PTM", body=f"{settings.app_name}: {text}")
        except Exception as exc:  # noqa: BLE001 - the meeting day is saved; a message problem must not undo it
            logger.warning("ptm_announce_failed", error=str(exc)[:200])

    async def get(self, ptm_id: uuid.UUID) -> dict:
        ptm = await self.session.get(PtmDay, ptm_id)
        if ptm is None:
            raise ResourceNotFoundError("Meeting day not found.")
        rows = (await self.session.execute(select(PtmSlot.employee_id, func.count(), func.count(PtmSlot.student_id)).where(PtmSlot.ptm_day_id == ptm.id)
                                           .group_by(PtmSlot.employee_id))).all()
        emps = {e.id: _name(e) for e in (await self.session.execute(select(Employee).where(Employee.id.in_([r[0] for r in rows] or [uuid.uuid4()])))).scalars()}
        return {"id": ptm.id, "day": ptm.day, "start_time": ptm.start_time.strftime("%H:%M"), "end_time": ptm.end_time.strftime("%H:%M"),
                "slot_minutes": ptm.slot_minutes, "note": ptm.note, "cancelled": ptm.cancelled,
                "teachers": sorted([{"employee_id": e, "name": emps.get(e, ""), "slots": n, "booked": b} for e, n, b in rows], key=lambda x: x["name"]),
                "slots": sum(r[1] for r in rows), "booked": sum(r[2] for r in rows)}

    async def list(self) -> list[dict]:
        ids = (await self.session.execute(select(PtmDay.id).order_by(PtmDay.day.desc()).limit(30))).scalars().all()
        return [await self.get(i) for i in ids]

    async def cancel(self, ptm_id: uuid.UUID, actor_id: uuid.UUID) -> dict:
        from app.notifications.family import _notify, family

        ptm = await self.session.get(PtmDay, ptm_id)
        if ptm is None:
            raise ResourceNotFoundError("Meeting day not found.")
        if ptm.cancelled:
            raise BusinessRuleViolationError("This meeting day is already cancelled.")
        ptm.cancelled = True
        booked = (await self.session.execute(select(PtmSlot).where(PtmSlot.ptm_day_id == ptm.id, PtmSlot.student_id.is_not(None)))).scalars().all()
        fam = await family(self.session, {s.student_id for s in booked})
        for s in booked:
            for uid, _l in (fam.get(s.student_id) or {"parents": []})["parents"]:
                await _notify(self.session, uid, "Parent-teacher meeting cancelled", f"The meeting on {ptm.day:%a %d %b} is cancelled.", "/dashboard/parent/meetings")
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="school.ptm_cancelled", entity_type="school_ptm_day", entity_id=ptm.id,
                             after={"day": ptm.day.isoformat(), "booked": len(booked)})
        return await self.get(ptm.id)

    async def for_parent(self, user, student_id: uuid.UUID) -> dict:
        from app.parents.service import ParentService

        await ParentService(self.session)._own_child(user.id, student_id)
        s = await self.session.get(Student, student_id)
        sec = await current_section(self.session, s)
        if sec is None or sec.class_teacher_id is None:
            return {"class_teacher": None, "days": []}
        teacher = await self.session.get(Employee, sec.class_teacher_id)
        days = (await self.session.execute(select(PtmDay).where(PtmDay.term_id == sec.term_id, PtmDay.cancelled.is_(False),
                                                                PtmDay.day >= institute_today()).order_by(PtmDay.day))).scalars().all()
        out = []
        for d in days:
            slots = (await self.session.execute(select(PtmSlot).where(PtmSlot.ptm_day_id == d.id, PtmSlot.employee_id == sec.class_teacher_id)
                                                .order_by(PtmSlot.start_time))).scalars().all()
            out.append({"id": d.id, "day": d.day, "note": d.note,
                        "slots": [{"id": x.id, "start_time": x.start_time.strftime("%H:%M"), "end_time": x.end_time.strftime("%H:%M"),
                                   "free": x.student_id is None, "mine": x.student_id == student_id} for x in slots],
                        "booked": next((x.start_time.strftime("%H:%M") for x in slots if x.student_id == student_id), None)})
        return {"class_teacher": _name(teacher), "days": out}

    async def book(self, user, slot_id: uuid.UUID, student_id: uuid.UUID) -> dict:
        from app.parents.service import ParentService

        await ParentService(self.session)._own_child(user.id, student_id)
        slot = (await self.session.execute(select(PtmSlot).where(PtmSlot.id == slot_id).with_for_update())).scalar_one_or_none()
        if slot is None:
            raise ResourceNotFoundError("That time is not available.")
        ptm = await self.session.get(PtmDay, slot.ptm_day_id)
        if ptm.cancelled or ptm.day < institute_today():
            raise BusinessRuleViolationError("This meeting day is over or cancelled.")
        s = await self.session.get(Student, student_id)
        sec = await current_section(self.session, s)
        if sec is None or sec.class_teacher_id != slot.employee_id:
            raise BusinessRuleViolationError("Book a time with your child's class teacher.")
        if slot.student_id is not None and slot.student_id != student_id:
            raise BusinessRuleViolationError("Someone has just booked this time. Please choose another.")
        old = (await self.session.execute(select(PtmSlot).where(PtmSlot.ptm_day_id == ptm.id, PtmSlot.student_id == student_id))).scalar_one_or_none()
        if old is not None and old.id != slot.id:
            old.student_id = old.booked_by = old.booked_at = None                 # moving to another time
            await self.session.flush()
        slot.student_id, slot.booked_by, slot.booked_at = student_id, user.id, datetime.now(timezone.utc)
        await self.session.flush()
        await self.audit.log(actor_user_id=user.id, action="school.ptm_booked", entity_type="school_ptm_slot", entity_id=slot.id,
                             after={"student_id": str(student_id), "time": slot.start_time.strftime("%H:%M")})
        return await self.for_parent(user, student_id)

    async def release(self, user, slot_id: uuid.UUID) -> dict:
        from app.parents.service import ParentService

        slot = await self.session.get(PtmSlot, slot_id)
        if slot is None or slot.student_id is None:
            raise ResourceNotFoundError("Booking not found.")
        sid = slot.student_id
        await ParentService(self.session)._own_child(user.id, sid)
        slot.student_id = slot.booked_by = slot.booked_at = None
        await self.session.flush()
        return await self.for_parent(user, sid)

    async def for_teacher(self, user) -> list[dict]:
        emp = await employee_of(self.session, user)
        if emp is None:
            return []
        rows = (await self.session.execute(select(PtmSlot, PtmDay).join(PtmDay, PtmDay.id == PtmSlot.ptm_day_id).where(
            PtmSlot.employee_id == emp.id, PtmDay.cancelled.is_(False), PtmDay.day >= institute_today() - timedelta(days=7))
            .order_by(PtmDay.day, PtmSlot.start_time))).all()
        studs = {s.id: s for s in (await self.session.execute(select(Student).where(
            Student.id.in_([x.student_id for x, _ in rows if x.student_id] or [uuid.uuid4()])))).scalars()}
        days: dict = {}
        for x, d in rows:
            days.setdefault(d.id, {"id": d.id, "day": d.day, "note": d.note, "slots": []})["slots"].append(
                {"id": x.id, "start_time": x.start_time.strftime("%H:%M"), "end_time": x.end_time.strftime("%H:%M"),
                 "student": _name(studs.get(x.student_id)) if x.student_id else None,
                 "roll_number": studs[x.student_id].roll_number if x.student_id in studs else None, "notes": x.notes, "attended": x.attended})
        return list(days.values())

    async def save_notes(self, user, slot_id: uuid.UUID, notes: str | None, attended: bool | None) -> dict:
        slot = await self.session.get(PtmSlot, slot_id)
        if slot is None:
            raise ResourceNotFoundError("Meeting not found.")
        emp = await employee_of(self.session, user)
        if not is_office(user) and (emp is None or emp.id != slot.employee_id):
            raise AuthorizationError("Only the class teacher can note this meeting.")
        slot.notes = (notes or "").strip()[:1000] or None
        slot.attended = attended
        await self.session.flush()
        return {"id": slot.id, "notes": slot.notes, "attended": slot.attended}
