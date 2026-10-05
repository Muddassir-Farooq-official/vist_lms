from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import (
    ClassSession,
    ClassSessionResource,
    ClassSessionStatus,
    Course,
    CourseOffering,
    CourseProgram,
    DayOfWeek,
    Enrollment,
    EnrollmentStatus,
    Period,
    TimetableSlot,
)


def attending_filter(today: date):
    """Is this student still taking the class? Needs CourseOffering joined to Enrollment.

    ENROLLED, of course. But publishing a result sheet marks the enrollment COMPLETED while the term is still running
    (classes, timetable and attendance carry on), so a COMPLETED enrollment also counts until the term is over: its
    end date has not passed, or (no end date set) the offering still has classes coming up."""
    upcoming = exists().where(ClassSession.course_offering_id == Enrollment.course_offering_id, ClassSession.session_date >= today,
                              ClassSession.status != ClassSessionStatus.CANCELLED)
    return or_(
        Enrollment.status == EnrollmentStatus.ENROLLED,
        and_(Enrollment.status == EnrollmentStatus.COMPLETED,
             or_(CourseOffering.term_end_date >= today, and_(CourseOffering.term_end_date.is_(None), upcoming))),
    )


class CourseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, course_id: uuid.UUID) -> Course | None:
        return await self.session.get(Course, course_id)

    async def get_by_code(self, code: str) -> Course | None:
        stmt = select(Course).where(func.lower(Course.code) == code.strip().lower())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(self, *, active_only: bool = False) -> list[Course]:
        stmt = select(Course).order_by(Course.code)
        if active_only:
            stmt = stmt.where(Course.is_active.is_(True))
        return list((await self.session.execute(stmt)).scalars())

    async def create(self, course: Course) -> Course:
        self.session.add(course)
        await self.session.flush()
        return course


class CourseProgramRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, course_id: uuid.UUID, program_id: uuid.UUID) -> CourseProgram | None:
        return await self.session.get(CourseProgram, (course_id, program_id))

    async def list_for_program(self, program_id: uuid.UUID) -> list[CourseProgram]:
        stmt = select(CourseProgram).where(CourseProgram.program_id == program_id).order_by(CourseProgram.semester_number)
        return list((await self.session.execute(stmt)).scalars())

    async def list_for_course(self, course_id: uuid.UUID) -> list[CourseProgram]:
        stmt = select(CourseProgram).where(CourseProgram.course_id == course_id)
        return list((await self.session.execute(stmt)).scalars())

    async def create(self, link: CourseProgram) -> CourseProgram:
        self.session.add(link)
        await self.session.flush()
        return link

    async def delete(self, link: CourseProgram) -> None:
        await self.session.delete(link)


class CourseOfferingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, offering_id: uuid.UUID) -> CourseOffering | None:
        return await self.session.get(CourseOffering, offering_id)

    async def get_by_course_and_term(
        self, course_id: uuid.UUID, term_label: str, section_label: str | None = None
    ) -> CourseOffering | None:
        """One offering = one SECTION of a course in a term, so the lookup key
        is (course, term, section). With section_label=None it returns the
        first section found (older callers that don't care which)."""
        stmt = select(CourseOffering).where(
            CourseOffering.course_id == course_id, CourseOffering.term_label == term_label
        )
        if section_label is not None:
            stmt = stmt.where(func.lower(CourseOffering.section_label) == section_label.lower())
        stmt = stmt.order_by(CourseOffering.section_label).limit(1)
        return (await self.session.execute(stmt)).scalars().first()

    async def canonical_term_label(self, term_label: str) -> str:
        """'fall  2026' / 'FALL 2026' / 'Fall 2026 ' must all be the SAME term.
        Collapses whitespace and, if a term already exists that differs only
        by case, returns that existing spelling so terms never split in two."""
        cleaned = " ".join(term_label.split())
        stmt = select(CourseOffering.term_label).where(func.lower(CourseOffering.term_label) == cleaned.lower()).limit(1)
        existing = (await self.session.execute(stmt)).scalars().first()
        return existing or cleaned

    async def list_by_term(self, term_label: str) -> list[CourseOffering]:
        stmt = (
            select(CourseOffering)
            .where(CourseOffering.term_label == term_label)
            .order_by(CourseOffering.section_label)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def list_by_teacher(self, teacher_id: uuid.UUID) -> list[CourseOffering]:
        stmt = select(CourseOffering).where(CourseOffering.assigned_teacher_id == teacher_id)
        return list((await self.session.execute(stmt)).scalars())

    async def list_by_course(self, course_id: uuid.UUID) -> list[CourseOffering]:
        """Every offering of this course, across every term — used by
        the student eligible-offerings endpoint to find any currently
        open section of a course in their curriculum."""
        stmt = select(CourseOffering).where(CourseOffering.course_id == course_id)
        return list((await self.session.execute(stmt)).scalars())

    async def list_distinct_terms(self) -> list[str]:
        """Every term label that has at least one offering — this is
        what populates the 'load' dropdown in the admin UI, so nobody
        has to remember/retype an exact term label to see what's
        already there."""
        stmt = select(CourseOffering.term_label).distinct().order_by(CourseOffering.term_label.desc())
        return list((await self.session.execute(stmt)).scalars())

    async def create(self, offering: CourseOffering) -> CourseOffering:
        self.session.add(offering)
        await self.session.flush()
        return offering

    async def enrolled_student_ids(self, offering_id: uuid.UUID) -> set[uuid.UUID]:
        stmt = select(Enrollment.student_id).where(
            Enrollment.course_offering_id == offering_id, Enrollment.status == EnrollmentStatus.ENROLLED
        )
        return set((await self.session.execute(stmt)).scalars())

    async def enrolled_count(self, offering_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(Enrollment).where(
            Enrollment.course_offering_id == offering_id, Enrollment.status == EnrollmentStatus.ENROLLED
        )
        return (await self.session.execute(stmt)).scalar_one()


class EnrollmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, enrollment_id: uuid.UUID) -> Enrollment | None:
        return await self.session.get(Enrollment, enrollment_id)

    async def get_by_student_and_offering(self, student_id: uuid.UUID, course_offering_id: uuid.UUID) -> Enrollment | None:
        stmt = select(Enrollment).where(
            Enrollment.student_id == student_id, Enrollment.course_offering_id == course_offering_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_student(self, student_id: uuid.UUID) -> list[Enrollment]:
        stmt = select(Enrollment).where(Enrollment.student_id == student_id).order_by(Enrollment.enrolled_at.desc())
        return list((await self.session.execute(stmt)).scalars())

    async def list_current_for_student(self, student_id: uuid.UUID) -> list[Enrollment]:
        """The classes this student is attending right now (see attending_filter) -- what their dashboard, timetable and attendance show."""
        from app.core.clock import institute_today

        stmt = (select(Enrollment).join(CourseOffering, Enrollment.course_offering_id == CourseOffering.id)
                .where(Enrollment.student_id == student_id, attending_filter(institute_today())).order_by(Enrollment.enrolled_at.desc()))
        return list((await self.session.execute(stmt)).scalars())

    async def list_for_offering(self, course_offering_id: uuid.UUID) -> list[Enrollment]:
        stmt = select(Enrollment).where(Enrollment.course_offering_id == course_offering_id)
        return list((await self.session.execute(stmt)).scalars())

    async def list_prior_for_course(self, student_id: uuid.UUID, course_id: uuid.UUID) -> list[Enrollment]:
        """Every past (non-active) enrollment this student has for the
        underlying course, across any offering/term — this is what
        retake-eligibility is checked against (approved decision: a
        completed OR withdrawn prior attempt, pass or fail, both make a
        later re-enrollment a retake)."""
        stmt = (
            select(Enrollment)
            .join(CourseOffering, Enrollment.course_offering_id == CourseOffering.id)
            .where(
                Enrollment.student_id == student_id,
                CourseOffering.course_id == course_id,
                Enrollment.status.in_([EnrollmentStatus.COMPLETED, EnrollmentStatus.WITHDRAWN]),
            )
        )
        return list((await self.session.execute(stmt)).scalars())

    async def get_active_for_student_and_course(self, student_id: uuid.UUID, course_id: uuid.UUID) -> Enrollment | None:
        """Is this student CURRENTLY enrolled in this course via ANY
        offering? Blocks the edge case of enrolling in two different
        terms' offerings of the same course at once, which the simple
        (student, offering) unique constraint alone wouldn't catch."""
        stmt = (
            select(Enrollment)
            .join(CourseOffering, Enrollment.course_offering_id == CourseOffering.id)
            .where(
                Enrollment.student_id == student_id,
                CourseOffering.course_id == course_id,
                Enrollment.status == EnrollmentStatus.ENROLLED,
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_active_for_student_in_term(self, student_id: uuid.UUID, term_label: str) -> list[Enrollment]:
        stmt = (
            select(Enrollment)
            .join(CourseOffering, Enrollment.course_offering_id == CourseOffering.id)
            .where(
                Enrollment.student_id == student_id,
                CourseOffering.term_label == term_label,
                Enrollment.status == EnrollmentStatus.ENROLLED,
            )
        )
        return list((await self.session.execute(stmt)).scalars())

    async def create(self, enrollment: Enrollment) -> Enrollment:
        self.session.add(enrollment)
        await self.session.flush()
        return enrollment


class PeriodRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, period_id: uuid.UUID) -> Period | None:
        return await self.session.get(Period, period_id)

    async def list_all(self) -> list[Period]:
        stmt = select(Period).order_by(Period.period_number)
        return list((await self.session.execute(stmt)).scalars())

    async def get_by_number(self, period_number: int) -> Period | None:
        stmt = select(Period).where(Period.period_number == period_number)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(self, period: Period) -> Period:
        self.session.add(period)
        await self.session.flush()
        return period

    async def delete(self, period: Period) -> None:
        await self.session.delete(period)


class TimetableSlotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, slot_id: uuid.UUID) -> TimetableSlot | None:
        return await self.session.get(TimetableSlot, slot_id)

    async def list_for_offering(self, course_offering_id: uuid.UUID) -> list[TimetableSlot]:
        stmt = select(TimetableSlot).where(TimetableSlot.course_offering_id == course_offering_id)
        return list((await self.session.execute(stmt)).scalars())

    async def list_for_day(self, day_of_week: DayOfWeek) -> list[TimetableSlot]:
        """Every slot across every offering that falls on this weekday —
        the candidate set TimetableService's conflict checks compare a
        new/edited slot against."""
        stmt = select(TimetableSlot).where(TimetableSlot.day_of_week == day_of_week)
        return list((await self.session.execute(stmt)).scalars())

    async def list_all(self) -> list[TimetableSlot]:
        """Every recurring slot in the institute — backs the admin's
        whole-timetable overview, not scoped to any one offering."""
        stmt = select(TimetableSlot).order_by(TimetableSlot.day_of_week, TimetableSlot.start_time)
        return list((await self.session.execute(stmt)).scalars())

    async def list_for_teacher(self, teacher_id: uuid.UUID) -> list[TimetableSlot]:
        """Every slot across every offering this teacher is assigned to
        — used by TimetableService.auto_assign to prefer days the
        teacher is already committed to, rather than spreading their
        week out further than necessary."""
        stmt = (
            select(TimetableSlot)
            .join(CourseOffering, TimetableSlot.course_offering_id == CourseOffering.id)
            .where(CourseOffering.assigned_teacher_id == teacher_id)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def create(self, slot: TimetableSlot) -> TimetableSlot:
        self.session.add(slot)
        await self.session.flush()
        return slot

    async def delete(self, slot: TimetableSlot) -> None:
        await self.session.delete(slot)


class ClassSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, session_id: uuid.UUID) -> ClassSession | None:
        return await self.session.get(ClassSession, session_id)

    async def list_for_offering(self, course_offering_id: uuid.UUID) -> list[ClassSession]:
        stmt = (
            select(ClassSession)
            .where(ClassSession.course_offering_id == course_offering_id)
            .order_by(ClassSession.session_date, ClassSession.start_time)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def list_for_slot(self, timetable_slot_id: uuid.UUID) -> list[ClassSession]:
        stmt = select(ClassSession).where(ClassSession.timetable_slot_id == timetable_slot_id)
        return list((await self.session.execute(stmt)).scalars())

    async def list_scheduled_from_slot_on_or_after(self, timetable_slot_id: uuid.UUID, on_or_after: date) -> list[ClassSession]:
        """Used by TimetableService.delete_slot — the not-yet-occurred,
        still-SCHEDULED sessions that get cancelled (not deleted) when
        their generating slot is removed. Past sessions, and ones
        already CANCELLED, are left alone."""
        stmt = select(ClassSession).where(
            ClassSession.timetable_slot_id == timetable_slot_id,
            ClassSession.session_date >= on_or_after,
            ClassSession.status == ClassSessionStatus.SCHEDULED,
        )
        return list((await self.session.execute(stmt)).scalars())

    async def create(self, class_session: ClassSession) -> ClassSession:
        self.session.add(class_session)
        await self.session.flush()
        return class_session


class ClassSessionResourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, resource_id: uuid.UUID) -> ClassSessionResource | None:
        return await self.session.get(ClassSessionResource, resource_id)

    async def list_for_session(self, class_session_id: uuid.UUID) -> list[ClassSessionResource]:
        stmt = (
            select(ClassSessionResource)
            .where(ClassSessionResource.class_session_id == class_session_id)
            .order_by(ClassSessionResource.created_at)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def create(self, resource: ClassSessionResource) -> ClassSessionResource:
        self.session.add(resource)
        await self.session.flush()
        return resource

    async def delete(self, resource: ClassSessionResource) -> None:
        await self.session.delete(resource)