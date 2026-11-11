"""Combined classes: sections of the SAME course in the same term that one teacher teaches together, at the
same time, e.g. AHS201 for BS Anesthesia (section A) and BS Dental (section B).

Each section stays its own record (its students, attendance, marks and result sheet), and they are linked by
CourseOffering.combined_group_id. Linking them means:
  - one teacher for all of them (set when combining, and kept the same when it changes);
  - one timetable: a slot added, moved or removed in one is done in all; the clash checks let them share the
    teacher and the room at the same time (TimetableService);
  - the marks scheme (Quiz 1, Mid Term ...) is the same in all, and kept the same (GradingService);
  - the teacher takes attendance and enters marks on one list with each student's program shown; each mark is
    saved to the student's own section (attendance router, faculty pages);
  - one exam paper for all of them (app/exams).
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import Course, CourseOffering, Enrollment, EnrollmentStatus, OfferingAssessment, TimetableSlot
from app.audit.service import AuditService
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError


async def members(session: AsyncSession, offering: CourseOffering | uuid.UUID) -> list[CourseOffering]:
    """Every section of this offering's combined class (itself included), A before B; just itself when not combined."""
    if not isinstance(offering, CourseOffering):
        offering = await session.get(CourseOffering, offering)
        if offering is None:
            return []
    if offering.combined_group_id is None:
        return [offering]
    return list((await session.execute(select(CourseOffering).where(CourseOffering.combined_group_id == offering.combined_group_id)
                                       .order_by(CourseOffering.section_label))).scalars())


async def member_ids(session: AsyncSession, offering_id: uuid.UUID) -> list[uuid.UUID]:
    return [o.id for o in await members(session, offering_id)]


def same_group(a: CourseOffering, b: CourseOffering) -> bool:
    return a.combined_group_id is not None and a.combined_group_id == b.combined_group_id


async def programs_of(session: AsyncSession, offering_ids) -> dict[uuid.UUID, list[str]]:
    """The programs of the students enrolled in each section (what the teacher sees: "BS Anesthesia")."""
    from app.programs.models import Program
    from app.students.models import Student

    out: dict[uuid.UUID, list[str]] = {i: [] for i in offering_ids}
    if not out:
        return out
    rows = (await session.execute(select(Enrollment.course_offering_id, Program.name, func.count()).join(Student, Student.id == Enrollment.student_id)
                                  .join(Program, Program.id == Student.program_id).where(Enrollment.course_offering_id.in_(list(out)),
                                                                                         Enrollment.status == EnrollmentStatus.ENROLLED)
                                  .group_by(Enrollment.course_offering_id, Program.name).order_by(func.count().desc()))).all()
    for oid, name, _n in rows:
        out[oid].append(name)
    return out


def _slot_key(s: TimetableSlot) -> tuple:
    return (s.day_of_week, s.start_time, s.end_time)


class CombinedClassService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def view(self, offering_id: uuid.UUID) -> dict:
        from app.employees.models import Employee

        off = await self.session.get(CourseOffering, offering_id)
        if off is None:
            raise ResourceNotFoundError("Course offering not found.")
        group = await members(self.session, off)
        others = list((await self.session.execute(select(CourseOffering).where(
            CourseOffering.course_id == off.course_id, func.lower(CourseOffering.term_label) == off.term_label.lower(),
            CourseOffering.id.not_in([g.id for g in group])).order_by(CourseOffering.section_label))).scalars())
        progs = await programs_of(self.session, [o.id for o in group + others])
        counts = dict((await self.session.execute(select(Enrollment.course_offering_id, func.count()).where(
            Enrollment.course_offering_id.in_([o.id for o in group + others]), Enrollment.status == EnrollmentStatus.ENROLLED)
            .group_by(Enrollment.course_offering_id))).all())
        teachers = {e.id: f"{e.first_name} {e.last_name}".strip() for e in (await self.session.execute(select(Employee).where(
            Employee.id.in_({o.assigned_teacher_id for o in group + others if o.assigned_teacher_id})))).scalars()}

        def row(o):
            return {"id": o.id, "section_label": o.section_label, "programs": progs.get(o.id, []), "students": counts.get(o.id, 0),
                    "teacher_id": o.assigned_teacher_id, "teacher": teachers.get(o.assigned_teacher_id)}
        return {"combined": off.combined_group_id is not None, "members": [row(o) for o in group], "can_join": [row(o) for o in others],
                "students": sum(counts.get(o.id, 0) for o in group)}

    async def combine(self, offering_id: uuid.UUID, other_ids: list[uuid.UUID], *, teacher_id: uuid.UUID | None, actor_id: uuid.UUID) -> dict:
        from app.academics.grading_service import GradingService
        from app.academics.service import TimetableService

        lead = await self.session.get(CourseOffering, offering_id)
        if lead is None:
            raise ResourceNotFoundError("Course offering not found.")
        others = []
        for oid in dict.fromkeys(other_ids):
            o = await self.session.get(CourseOffering, oid)
            if o is None or o.id == lead.id:
                raise ValidationAppError("Choose other sections to combine with.")
            if o.course_id != lead.course_id or o.term_label.lower() != lead.term_label.lower():
                raise BusinessRuleViolationError("Only sections of the same course in the same term can be combined.")
            others.append(o)
        if not others:
            raise ValidationAppError("Choose at least one other section.")
        group: dict[uuid.UUID, CourseOffering] = {}
        for o in [lead, *others]:
            for m in await members(self.session, o):          # joining a section that is already combined brings its group along
                group[m.id] = m
        everyone = list(group.values())

        # one teacher
        teachers = {o.assigned_teacher_id for o in everyone if o.assigned_teacher_id}
        if teacher_id is None:
            if len(teachers) > 1:
                raise BusinessRuleViolationError("These sections have different teachers. Choose the teacher of the combined class.")
            teacher_id = next(iter(teachers), None)

        # one timetable: sections without a timetable get the lead's; a section with a different one is refused
        slots = {o.id: list((await self.session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id == o.id))).scalars())
                 for o in everyone}
        source = max(everyone, key=lambda o: (o.id == lead.id and bool(slots[o.id]), len(slots[o.id])))
        pattern = sorted(_slot_key(s) for s in slots[source.id])
        for o in everyone:
            if slots[o.id] and sorted(_slot_key(s) for s in slots[o.id]) != pattern:
                raise BusinessRuleViolationError(f"Section {o.section_label} already has a different timetable. Remove its timetable "
                                                 f"first (or make it the same as section {source.section_label}), then combine.")

        # one marks scheme
        schemes = {o.id: {a.assessment_type_id for a in (await self.session.execute(select(OfferingAssessment).where(
            OfferingAssessment.course_offering_id == o.id))).scalars()} for o in everyone}
        scheme_src = max(everyone, key=lambda o: len(schemes[o.id]))
        for o in everyone:
            if schemes[o.id] and schemes[o.id] != schemes[scheme_src.id]:
                raise BusinessRuleViolationError(f"Section {o.section_label} has a different marks scheme from section {scheme_src.section_label}. "
                                                 "Make them the same first.")

        gid = next((o.combined_group_id for o in everyone if o.combined_group_id), None) or uuid.uuid4()
        for o in everyone:
            o.combined_group_id = gid
            o.assigned_teacher_id = teacher_id
        await self.session.flush()
        tt = TimetableService(self.session)
        if teacher_id:
            await tt.assert_teacher_free_for_offering(source, teacher_id)
        for o in everyone:
            if not slots[o.id]:
                for s in slots[source.id]:
                    await tt.create_slot(course_offering_id=o.id, period_id=s.period_id, day_of_week=s.day_of_week, room=s.room,
                                         actor_id=actor_id, mirror=False)
            for type_id in schemes[scheme_src.id] - schemes[o.id]:
                await GradingService(self.session).add_offering_assessment(o.id, assessment_type_id=type_id, actor_id=actor_id, mirror=False)
        course = await self.session.get(Course, lead.course_id)
        await self.audit.log(actor_user_id=actor_id, action="offering.combined", entity_type="course_offering", entity_id=lead.id,
                             after={"course": course.code if course else None, "term": lead.term_label,
                                    "sections": [o.section_label for o in sorted(everyone, key=lambda x: x.section_label)],
                                    "teacher_id": str(teacher_id) if teacher_id else None})
        return await self.view(lead.id)

    async def separate(self, offering_id: uuid.UUID, *, actor_id: uuid.UUID) -> dict:
        off = await self.session.get(CourseOffering, offering_id)
        if off is None:
            raise ResourceNotFoundError("Course offering not found.")
        if off.combined_group_id is None:
            raise BusinessRuleViolationError("This section is not part of a combined class.")
        rest = [m for m in await members(self.session, off) if m.id != off.id]
        off.combined_group_id = None
        if len(rest) == 1:
            rest[0].combined_group_id = None
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="offering.separated", entity_type="course_offering", entity_id=off.id,
                             after={"section": off.section_label, "term": off.term_label})
        return await self.view(off.id)
