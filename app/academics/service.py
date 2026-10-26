from __future__ import annotations

from app.core.clock import institute_today

import itertools
import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select

from app.core.config import settings
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import (
    ClassSession,
    ClassSessionResource,
    ClassSessionStatus,
    Course,
    CourseOffering,
    CourseProgram,
    DAY_OF_WEEK_BY_PYTHON_WEEKDAY,
    DayOfWeek,
    Enrollment,
    EnrollmentOutcome,
    EnrollmentStatus,
    LectureResourceType,
    Period,
    TimetableSlot,
)
from app.academics.repository import (
    ClassSessionRepository,
    ClassSessionResourceRepository,
    CourseOfferingRepository,
    CourseProgramRepository,
    CourseRepository,
    EnrollmentRepository,
    PeriodRepository,
    TimetableSlotRepository,
)
from app.audit.service import AuditService
from app.auth.models import Role, UserRole
from app.auth.permissions import Roles
from app.core.exceptions import BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError
from app.core.storage import ALLOWED_PDF_CONTENT_TYPES, MAX_CERTIFICATE_PDF_BYTES, delete_file, save_uploaded_file
from app.employees.models import Employee
from app.programs.models import Program
from app.students.models import Student, StudentStatus


class CourseService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = CourseRepository(session)
        self.program_link_repo = CourseProgramRepository(session)
        self.audit = AuditService(session)

    async def get(self, course_id: uuid.UUID) -> Course:
        course = await self.repo.get_by_id(course_id)
        if course is None:
            raise ResourceNotFoundError("Course not found.")
        return course

    async def create(self, *, code: str, title: str, credit_hours: int, description: str | None, actor_id: uuid.UUID) -> Course:
        if await self.repo.get_by_code(code):
            raise DuplicateResourceError(f"A course with code '{code}' already exists.")
        course = Course(code=code.strip().upper(), title=title.strip(), credit_hours=credit_hours, description=description)
        await self.repo.create(course)
        await self.audit.log(actor_user_id=actor_id, action="course.created", entity_type="course", entity_id=course.id, after={"code": course.code})
        return course

    async def update(
        self, course_id: uuid.UUID, *, title: str | None = None, credit_hours: int | None = None,
        description: str | None = ..., is_active: bool | None = None, actor_id: uuid.UUID,
    ) -> Course:
        """Deliberately does NOT allow changing `code` — same reasoning
        as roll_number/employee_code elsewhere in this codebase: a code
        is how the course is referenced everywhere (offerings, curricula,
        transcripts), so changing it after creation is a rename
        operation with far wider blast radius than a normal edit.
        `description` uses the `...` sentinel (not None) so an explicit
        null can clear it, distinct from 'field not provided'."""
        course = await self.get(course_id)
        before = {"title": course.title, "credit_hours": course.credit_hours, "is_active": course.is_active}

        if title is not None:
            course.title = title.strip()
        if credit_hours is not None:
            course.credit_hours = credit_hours
        if description is not ...:
            course.description = description
        if is_active is not None:
            course.is_active = is_active

        await self.audit.log(
            actor_user_id=actor_id, action="course.updated", entity_type="course", entity_id=course.id,
            before=before, after={"title": course.title, "credit_hours": course.credit_hours, "is_active": course.is_active},
        )
        return course

    async def link_to_program(self, course_id: uuid.UUID, *, program_id: uuid.UUID, semester_number: int, actor_id: uuid.UUID) -> CourseProgram:
        course = await self.get(course_id)
        program = await self.session.get(Program, program_id)
        if program is None:
            raise ResourceNotFoundError("Program not found.")
        if await self.program_link_repo.get(course_id, program_id):
            raise DuplicateResourceError(f"'{course.code}' is already linked to this program.")
        if semester_number > program.total_semesters:
            raise BusinessRuleViolationError(
                f"Semester {semester_number} exceeds this program's {program.total_semesters} total semesters."
            )
        link = CourseProgram(course_id=course_id, program_id=program_id, semester_number=semester_number)
        await self.program_link_repo.create(link)
        await self.audit.log(
            actor_user_id=actor_id, action="course.linked_to_program", entity_type="course", entity_id=course_id,
            after={"program_id": str(program_id), "semester_number": semester_number},
        )
        return link

    async def unlink_from_program(self, course_id: uuid.UUID, program_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        link = await self.program_link_repo.get(course_id, program_id)
        if link is None:
            raise ResourceNotFoundError("This course is not linked to that program.")
        await self.program_link_repo.delete(link)
        await self.audit.log(actor_user_id=actor_id, action="course.unlinked_from_program", entity_type="course", entity_id=course_id)


class CourseOfferingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = CourseOfferingRepository(session)
        self.course_repo = CourseRepository(session)
        self.audit = AuditService(session)

    async def get(self, offering_id: uuid.UUID) -> CourseOffering:
        offering = await self.repo.get_by_id(offering_id)
        if offering is None:
            raise ResourceNotFoundError("Course offering not found.")
        return offering

    async def _get_teacher_or_raise(self, assigned_teacher_id: uuid.UUID) -> Employee:
        """Confirms the employee exists AND actually holds the TEACHER
        RBAC role — nothing previously stopped assigning a librarian,
        accountant, or admin as an offering's 'teacher'. Checked against
        the employee's real, current role assignment (UserRole), not
        their job role's *default* role — a job role's default is only
        what a new hire gets automatically; the live assignment is the
        one that actually governs what they can do."""
        teacher = await self.session.get(Employee, assigned_teacher_id)
        if teacher is None:
            raise ResourceNotFoundError("Assigned teacher (employee) not found.")
        if teacher.user_id is None:
            raise BusinessRuleViolationError("This employee has no login account and cannot be assigned as a teacher.")

        stmt = (
            select(UserRole)
            .join(Role, UserRole.role_id == Role.id)
            .where(UserRole.user_id == teacher.user_id, Role.code == Roles.TEACHER)
        )
        has_teacher_role = (await self.session.execute(stmt)).scalar_one_or_none() is not None
        if not has_teacher_role:
            raise BusinessRuleViolationError(
                f"{teacher.first_name} {teacher.last_name} does not hold the Teacher role and cannot be assigned to a course offering."
            )
        return teacher

    async def create(
        self, *, course_id: uuid.UUID, term_label: str, assigned_teacher_id: uuid.UUID | None,
        term_start_date: date | None = None, term_end_date: date | None = None,
        section_label: str = "A", capacity: int | None = None, actor_id: uuid.UUID,
    ) -> CourseOffering:
        course = await self.course_repo.get_by_id(course_id)
        if course is None:
            raise ResourceNotFoundError("Course not found.")
        term_label = await self.repo.canonical_term_label(term_label)
        section_label = " ".join((section_label or "A").split()) or "A"
        if capacity is not None and capacity <= 0:
            raise BusinessRuleViolationError("Capacity must be a positive number (or left empty for unlimited).")
        if await self.repo.get_by_course_and_term(course_id, term_label, section_label):
            raise DuplicateResourceError(
                f"'{course.code}' already has section '{section_label}' for {term_label}."
            )
        if assigned_teacher_id is not None:
            await self._get_teacher_or_raise(assigned_teacher_id)
        if term_start_date is not None and term_end_date is not None and term_start_date > term_end_date:
            raise BusinessRuleViolationError("term_start_date must be before term_end_date.")

        offering = CourseOffering(
            course_id=course_id, term_label=term_label, section_label=section_label, capacity=capacity,
            assigned_teacher_id=assigned_teacher_id,
            term_start_date=term_start_date, term_end_date=term_end_date,
        )
        await self.repo.create(offering)
        await self.audit.log(
            actor_user_id=actor_id, action="course_offering.created", entity_type="course_offering", entity_id=offering.id,
            after={"course_id": str(course_id), "term_label": term_label, "section_label": section_label},
        )
        return offering

    async def set_content_lock(
        self, offering_id: uuid.UUID, *, locked: bool, scope: str, actor_id: uuid.UUID
    ) -> tuple[CourseOffering, int]:
        """Admin: protect (or release) course content from the assigned teacher.
        Returns the offering and how many classes were individually changed."""
        offering = await self.get(offering_id)
        now = datetime.now(timezone.utc)
        changed = 0
        if scope == "COURSE":
            offering.content_locked_at = now if locked else None
        else:
            today = institute_today()
            for class_session in await ClassSessionRepository(self.session).list_for_offering(offering_id):
                if scope == "PAST_CLASSES" and class_session.session_date >= today:
                    continue
                already = class_session.content_locked_at is not None
                if locked and not already:
                    class_session.content_locked_at = now
                    changed += 1
                elif not locked and already:
                    class_session.content_locked_at = None
                    changed += 1
        await self.audit.log(
            actor_user_id=actor_id, action="course_offering.content_locked" if locked else "course_offering.content_unlocked",
            entity_type="course_offering", entity_id=offering_id, metadata={"scope": scope, "classes_changed": changed},
        )
        return offering, changed

    async def generate_for_program_semester(
        self, *, term_label: str, program_id: uuid.UUID, semester_number: int, section_label: str = "A",
        term_start_date: date | None, term_end_date: date | None, capacity: int | None = None,
        open_for_enrollment: bool = False, actor_id: uuid.UUID,
    ) -> dict:
        """Opens a whole semester in one action: creates one offering for EVERY
        active course in this program's curriculum at this semester (skipping
        any that already exist). Instead of creating 6-8 offerings by hand per
        program per semester."""
        program = await self.session.get(Program, program_id)
        if program is None:
            raise ResourceNotFoundError("Program not found.")
        if semester_number < 1 or semester_number > program.total_semesters:
            raise BusinessRuleViolationError(
                f"Semester {semester_number} is outside this program's 1-{program.total_semesters} range."
            )
        if term_start_date is not None and term_end_date is not None and term_start_date > term_end_date:
            raise BusinessRuleViolationError("term_start_date must be before term_end_date.")
        links = [
            link for link in await CourseProgramRepository(self.session).list_for_program(program_id)
            if link.semester_number == semester_number
        ]
        if not links:
            raise BusinessRuleViolationError(
                f"No courses are linked to {program.code} semester {semester_number}. Link the curriculum first."
            )
        term_label = await self.repo.canonical_term_label(term_label)
        section_label = " ".join((section_label or "A").split()) or "A"
        created, skipped = [], []
        for link in sorted(links, key=lambda l: str(l.course_id)):
            course = await self.course_repo.get_by_id(link.course_id)
            if course is None or not course.is_active:
                skipped.append({"course_code": course.code if course else "?", "reason": "course is inactive"})
                continue
            if await self.repo.get_by_course_and_term(course.id, term_label, section_label):
                skipped.append({"course_code": course.code, "reason": "already exists"})
                continue
            offering = CourseOffering(
                course_id=course.id, term_label=term_label, section_label=section_label, capacity=capacity,
                term_start_date=term_start_date, term_end_date=term_end_date,
                is_open_for_enrollment=open_for_enrollment,
            )
            await self.repo.create(offering)
            created.append({"offering_id": str(offering.id), "course_code": course.code})
        await self.audit.log(
            actor_user_id=actor_id, action="course_offering.generated_for_semester", entity_type="course_offering",
            entity_id=None,
            metadata={"program": program.code, "semester": semester_number, "term": term_label,
                      "section": section_label, "created": len(created), "skipped": len(skipped)},
        )
        return {"term_label": term_label, "section_label": section_label, "created": created, "skipped": skipped}

    async def delete_offering(self, offering_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        """Remove an offering created by mistake. Only while it is empty: no student has ever enrolled,
        no attendance was taken and no exam paper refers to it. Anything with history stays (close it instead)."""
        from app.academics.models import Enrollment as _E
        from app.attendance.models import AttendanceRecord
        from app.exams.models import ExamPaper

        offering = await self.repo.get_by_id(offering_id)
        if offering is None:
            raise ResourceNotFoundError("Course offering not found.")
        if (await self.session.execute(select(_E.id).where(_E.course_offering_id == offering_id).limit(1))).first():
            raise BusinessRuleViolationError("Students have enrolled in this offering, so it cannot be deleted. Close it to enrollment instead.")
        if (await self.session.execute(select(AttendanceRecord.id).join(ClassSession, ClassSession.id == AttendanceRecord.class_session_id)
                                       .where(ClassSession.course_offering_id == offering_id).limit(1))).first():
            raise BusinessRuleViolationError("Attendance was taken for this offering, so it cannot be deleted.")
        if (await self.session.execute(select(ExamPaper.id).where(ExamPaper.course_offering_id == offering_id).limit(1))).first():
            raise BusinessRuleViolationError("An exam paper is scheduled for this offering. Remove it from the datesheet first.")
        await self.session.delete(offering)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="course_offering.deleted", entity_type="course_offering", entity_id=offering_id,
                             before={"term_label": offering.term_label, "section": offering.section_label, "course_id": str(offering.course_id)})

    async def update(
        self, offering_id: uuid.UUID, *, assigned_teacher_id: uuid.UUID | None = ..., is_open_for_enrollment: bool | None = None,
        term_start_date: date | None = ..., term_end_date: date | None = ..., actor_id: uuid.UUID,
        capacity: int | None = ..., section_label: str | None = None,
    ) -> CourseOffering:
        offering = await self.get(offering_id)
        before = {"assigned_teacher_id": str(offering.assigned_teacher_id) if offering.assigned_teacher_id else None, "is_open_for_enrollment": offering.is_open_for_enrollment,
                  "capacity": offering.capacity, "section_label": offering.section_label}

        if assigned_teacher_id is not ...:  # sentinel: only touch it if the caller actually passed something (including None to unassign)
            if assigned_teacher_id is not None:
                await self._get_teacher_or_raise(assigned_teacher_id)
                # Re-assigning a teacher to an offering that ALREADY has timetable
                # slots must not silently double-book that teacher.
                await TimetableService(self.session).assert_teacher_free_for_offering(offering, assigned_teacher_id)
            offering.assigned_teacher_id = assigned_teacher_id
        if capacity is not ...:
            if capacity is not None:
                if capacity <= 0:
                    raise BusinessRuleViolationError("Capacity must be a positive number (or empty for unlimited).")
                current = await self.repo.enrolled_count(offering.id)
                if capacity < current:
                    raise BusinessRuleViolationError(
                        f"{current} students are already enrolled; capacity cannot be set below that."
                    )
            offering.capacity = capacity
        if section_label is not None:
            new_label = " ".join(section_label.split())
            if not new_label:
                raise BusinessRuleViolationError("Section label cannot be empty.")
            if new_label.lower() != offering.section_label.lower():
                clash = await self.repo.get_by_course_and_term(offering.course_id, offering.term_label, new_label)
                if clash is not None and clash.id != offering.id:
                    raise DuplicateResourceError(f"Section '{new_label}' already exists for this course and term.")
            offering.section_label = new_label
        if is_open_for_enrollment is not None:
            offering.is_open_for_enrollment = is_open_for_enrollment
        if term_start_date is not ...:
            offering.term_start_date = term_start_date
        if term_end_date is not ...:
            offering.term_end_date = term_end_date
        if (
            offering.term_start_date is not None and offering.term_end_date is not None
            and offering.term_start_date > offering.term_end_date
        ):
            raise BusinessRuleViolationError("term_start_date must be before term_end_date.")

        await self.audit.log(
            actor_user_id=actor_id, action="course_offering.updated", entity_type="course_offering", entity_id=offering.id,
            before=before,
            after={"assigned_teacher_id": str(offering.assigned_teacher_id) if offering.assigned_teacher_id else None, "is_open_for_enrollment": offering.is_open_for_enrollment,
                   "capacity": offering.capacity, "section_label": offering.section_label},
        )
        return offering

    async def upload_syllabus(self, offering_id: uuid.UUID, *, content: bytes, original_filename: str, content_type: str, actor_id: uuid.UUID) -> CourseOffering:
        offering = await self.get(offering_id)
        try:
            storage_key, safe_name = save_uploaded_file(
                category="course-syllabi", entity_id=offering.id, content=content,
                original_filename=original_filename, content_type=content_type,
                allowed_content_types=ALLOWED_PDF_CONTENT_TYPES, max_bytes=MAX_CERTIFICATE_PDF_BYTES,
            )
        except ValueError as exc:
            raise BusinessRuleViolationError(str(exc)) from exc

        delete_file(offering.syllabus_storage_key)
        offering.syllabus_storage_key = storage_key
        offering.syllabus_original_name = safe_name
        await self.audit.log(actor_user_id=actor_id, action="course_offering.syllabus_uploaded", entity_type="course_offering", entity_id=offering.id)
        return offering

    async def remove_syllabus(self, offering_id: uuid.UUID, *, actor_id: uuid.UUID) -> CourseOffering:
        offering = await self.get(offering_id)
        delete_file(offering.syllabus_storage_key)
        offering.syllabus_storage_key = None
        offering.syllabus_original_name = None
        await self.audit.log(actor_user_id=actor_id, action="course_offering.syllabus_removed", entity_type="course_offering", entity_id=offering.id)
        return offering

    async def can_manage(self, offering: CourseOffering, *, is_admin: bool, own_teacher_id: uuid.UUID | None) -> bool:
        """Shared ownership check: ADMIN always can; a TEACHER only for
        the offering they're actually assigned to. Same role-based
        scoping discipline as app/students/permissions.py — a bare
        LECTURE_MANAGE permission string doesn't imply 'any offering',
        since every TEACHER holds that permission."""
        return is_admin or (own_teacher_id is not None and offering.assigned_teacher_id == own_teacher_id)


class EnrollmentService:
    """The core business logic: what makes an enrollment valid. See the
    approved plan (conversation history) for the exact rules — summarized
    at each check below."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.offering_repo = CourseOfferingRepository(session)
        self.enrollment_repo = EnrollmentRepository(session)
        self.program_link_repo = CourseProgramRepository(session)
        self.audit = AuditService(session)

    async def _refuse_finished_offering(self, offering) -> None:
        """No one joins a course whose attendance is final or whose result is already decided."""
        from app.academics.models import ResultSheet, ResultSheetStatus

        if getattr(offering, "attendance_locked_at", None) is not None:
            raise BusinessRuleViolationError("This course's attendance is already locked (final), so nobody can join it now.")
        decided = (await self.session.execute(select(ResultSheet.status).where(ResultSheet.course_offering_id == offering.id))).scalar_one_or_none()
        if decided in (ResultSheetStatus.APPROVED, ResultSheetStatus.PUBLISHED):
            raise BusinessRuleViolationError("This course's result is already approved or published, so nobody can join it now.")

    async def enroll(
        self, *, student_id: uuid.UUID, course_offering_id: uuid.UUID, actor_id: uuid.UUID, by_admin: bool = False
    ) -> Enrollment:
        """by_admin=True lets the registrar enroll a student even while the
        offering is closed for SELF-enrollment (every other rule -- curriculum,
        credit cap, capacity, clashes -- still applies)."""
        offering = await self.offering_repo.get_by_id(course_offering_id)
        if offering is None:
            raise ResourceNotFoundError("Course offering not found.")
        if not by_admin and not offering.is_open_for_enrollment:
            raise BusinessRuleViolationError("This course is not currently open for enrollment.")
        await self._refuse_finished_offering(offering)

        student = await self.session.get(Student, student_id)
        if student is None:
            raise ResourceNotFoundError("Student not found.")
        if student.status != StudentStatus.ACTIVE:
            raise BusinessRuleViolationError(
                f"Only active students can enroll in courses — this student's status is {student.status.value}."
            )

        course = await self.session.get(Course, offering.course_id)  # explicit fetch, not offering.course — see app/academics/router.py's comments for why relationship access here is a real, proven MissingGreenlet risk
        if course is None:
            raise ResourceNotFoundError("Course not found for this offering.")
        if not course.is_active:
            raise BusinessRuleViolationError(f"'{course.code}' has been deactivated and is no longer open for enrollment.")

        # Re-adding after a withdrawal reuses the same row (the unique
        # constraint on (student, offering) means a second INSERT for the
        # same pair would fail outright) rather than creating a new one.
        existing = await self.enrollment_repo.get_by_student_and_offering(student_id, course_offering_id)
        if existing is not None:
            if existing.status == EnrollmentStatus.ENROLLED:
                raise BusinessRuleViolationError("You are already enrolled in this course.")
            if existing.status == EnrollmentStatus.COMPLETED:
                raise BusinessRuleViolationError(
                    "This offering is already completed for you and can't be re-enrolled in — "
                    "look for a later term's offering to retake it instead."
                )
            # else: WITHDRAWN — falls through, re-validated and re-activated below.

        # Can't be concurrently ENROLLED in the same underlying course via
        # a different offering (e.g. two terms open at once by mistake).
        active_elsewhere = await self.enrollment_repo.get_active_for_student_and_course(student_id, offering.course_id)
        if active_elsewhere is not None and (existing is None or active_elsewhere.id != existing.id):
            raise BusinessRuleViolationError("You are already actively enrolled in this course via a different offering.")

        # Retake eligibility: ANY prior Completed or Withdrawn attempt at
        # this course (pass, fail, or dropped — doesn't matter which)
        # makes this a retake, per approved decision. A retake may be
        # taken in any semester; a first-time attempt must match the
        # student's own program's curriculum slot for this course.
        prior_attempts = await self.enrollment_repo.list_prior_for_course(student_id, offering.course_id)
        # Re-joining the very section one withdrew from is the same attempt, not a retake.
        prior_attempts = [p for p in prior_attempts if existing is None or p.id != existing.id]
        is_retake = len(prior_attempts) > 0

        if not is_retake:
            if student.program_id is None:
                raise BusinessRuleViolationError("You must be assigned to a program before enrolling in courses.")
            course_program = await self.program_link_repo.get(offering.course_id, student.program_id)
            if course_program is None:
                raise BusinessRuleViolationError("This course is not part of your program's curriculum.")
            if course_program.semester_number != student.current_semester:
                raise BusinessRuleViolationError(
                    f"'{course.code}' is offered at semester {course_program.semester_number} in your program, "
                    f"but you are currently at semester {student.current_semester}. You can only enroll in your "
                    f"current semester's courses (or retake a course you've already attempted before, in any semester)."
                )

        # Section rule: a student who belongs to Section B attends Section B's
        # offerings (first attempts only -- a retake may join whichever
        # section suits their timetable).
        if (
            not is_retake and student.section_label
            and offering.section_label.strip().lower() != student.section_label.strip().lower()
        ):
            raise BusinessRuleViolationError(
                f"'{course.code}' section '{offering.section_label}' is not your section "
                f"('{student.section_label}'). Enroll in section '{student.section_label}' instead."
            )

        # Capacity -- the offering row is locked first, so two students taking the last seat at
        # the same moment cannot both get it.
        if offering.capacity is not None:
            from app.academics.models import CourseOffering as _CO

            await self.session.execute(select(_CO.id).where(_CO.id == offering.id).with_for_update())
            already = await self.offering_repo.enrolled_count(offering.id)
            if already >= offering.capacity:
                raise BusinessRuleViolationError(
                    f"'{course.code}' section '{offering.section_label}' is full ({offering.capacity} seats)."
                )

        # Timetable clash: the student cannot be in two classes at once.
        clash = await TimetableService(self.session).student_clash(student_id, offering, ignore_enrollment_id=existing.id if existing else None)
        if clash:
            raise BusinessRuleViolationError(clash)

        # Credit-hour cap — per the student's OWN program, not the
        # course's (approved decision: configurable per program).
        program = await self.session.get(Program, student.program_id) if student.program_id else None
        if program is None:
            raise BusinessRuleViolationError("You must be assigned to a program before enrolling in courses.")

        current_term_enrollments = await self.enrollment_repo.list_active_for_student_in_term(student_id, offering.term_label)
        # Exclude the row being re-activated from its own "current total" —
        # otherwise a withdraw-then-re-add would double-count it.
        current_term_enrollments = [e for e in current_term_enrollments if existing is None or e.id != existing.id]
        # Explicit fetches, not e.course_offering.course — same
        # discipline as everywhere else in this file now, no exceptions.
        current_credit_total = 0
        for e in current_term_enrollments:
            other_offering = await self.offering_repo.get_by_id(e.course_offering_id)
            other_course = await self.session.get(Course, other_offering.course_id) if other_offering else None
            current_credit_total += other_course.credit_hours if other_course else 0
        prospective_total = current_credit_total + course.credit_hours

        if prospective_total > program.max_credit_hours_per_semester:
            raise BusinessRuleViolationError(
                f"Enrolling in '{course.code}' ({course.credit_hours} credit hours) would bring your total for "
                f"{offering.term_label} to {prospective_total}, exceeding your program's "
                f"{program.max_credit_hours_per_semester}-hour limit."
            )

        now = datetime.now(timezone.utc)
        if existing is not None:
            existing.status = EnrollmentStatus.ENROLLED
            existing.is_retake = is_retake
            existing.enrolled_at = now
            enrollment = existing
        else:
            enrollment = Enrollment(
                student_id=student_id, course_offering_id=course_offering_id,
                status=EnrollmentStatus.ENROLLED, is_retake=is_retake, enrolled_at=now,
            )
            await self.enrollment_repo.create(enrollment)

        await self.audit.log(
            actor_user_id=actor_id, action="enrollment.created" if existing is None else "enrollment.re_enrolled",
            entity_type="enrollment", entity_id=enrollment.id,
            after={"course_offering_id": str(course_offering_id), "is_retake": is_retake},
        )

        # Subject/course-level fees (approved decision: charged on
        # EVERY enrollment attempt, including retakes) — deferred import,
        # same cross-module pattern as the fee-on-advancement fix in
        # StudentService.advance_semester_bulk.
        from app.fees.service import FeeStructureService

        await FeeStructureService(self.session).assign_course_fee_for_enrollment(
            student_id=student_id, course_id=offering.course_id, enrollment_id=enrollment.id, actor_id=actor_id
        )

        return enrollment

    async def enroll_many(
        self, *, course_offering_id: uuid.UUID, actor_id: uuid.UUID, student_ids: list[uuid.UUID] | None = None,
        program_id: uuid.UUID | None = None, semester_number: int | None = None, section_label: str | None = None,
    ) -> dict:
        """Registrar batch enrollment: either an explicit list of students, or
        every ACTIVE student matching program / semester / section. Each
        student is attempted independently (savepoint), so one ineligible
        student never blocks the rest -- failures come back with the reason."""
        offering = await self.offering_repo.get_by_id(course_offering_id)
        if offering is None:
            raise ResourceNotFoundError("Course offering not found.")
        if not student_ids and program_id is None and semester_number is None and section_label is None:
            raise BusinessRuleViolationError("Choose students, or a program / semester / section to enroll.")

        if student_ids:
            students = [st for st in [await self.session.get(Student, sid) for sid in student_ids] if st is not None]
        else:
            stmt = select(Student).where(Student.status == StudentStatus.ACTIVE)
            if program_id is not None:
                stmt = stmt.where(Student.program_id == program_id)
            if semester_number is not None:
                stmt = stmt.where(Student.current_semester == semester_number)
            if section_label:
                stmt = stmt.where(func.lower(Student.section_label) == section_label.strip().lower())
            students = list((await self.session.execute(stmt.order_by(Student.roll_number).limit(501))).scalars())
        if len(students) > 500:
            raise BusinessRuleViolationError("More than 500 students matched -- narrow the filter (program/semester/section).")

        enrolled, already, failed = 0, 0, []
        for st in students:
            existing = await self.enrollment_repo.get_by_student_and_offering(st.id, offering.id)
            if existing is not None and existing.status == EnrollmentStatus.ENROLLED:
                already += 1
                continue
            try:
                async with self.session.begin_nested():
                    await self.enroll(student_id=st.id, course_offering_id=offering.id, actor_id=actor_id, by_admin=True)
                enrolled += 1
            except (BusinessRuleViolationError, ResourceNotFoundError) as exc:
                failed.append({"roll_number": st.roll_number, "name": f"{st.first_name} {st.last_name}", "reason": exc.message})
        await self.audit.log(
            actor_user_id=actor_id, action="enrollment.batch_enrolled", entity_type="course_offering", entity_id=offering.id,
            metadata={"enrolled": enrolled, "already_enrolled": already, "failed": len(failed)},
        )
        return {"enrolled": enrolled, "already_enrolled": already, "failed": failed[:200], "failed_count": len(failed)}

    async def explain_self_enrollment(self, *, student_id: uuid.UUID, offering_id: uuid.UUID, actor_id: uuid.UUID) -> dict:
        """Would enroll() accept this student into this offering right now, and if not, why?

        It runs the REAL enroll() rules inside a savepoint that is always rolled back, so the answer can
        never drift from what really happens when the student presses Enroll -- and nothing is saved."""
        class _DryRun(Exception):
            pass

        prior = await self.enrollment_repo.list_prior_for_course(
            student_id, (await self.offering_repo.get_by_id(offering_id)).course_id
        )
        is_retake = len(prior) > 0
        current = await self.enrollment_repo.get_by_student_and_offering(student_id, offering_id)
        already = current is not None and current.status == EnrollmentStatus.ENROLLED
        try:
            async with self.session.begin_nested():
                await self.enroll(student_id=student_id, course_offering_id=offering_id, actor_id=actor_id, by_admin=False)
                raise _DryRun()
        except _DryRun:
            return {"can_enroll": True, "reason": None, "is_retake": is_retake, "already_enrolled": False}
        except (BusinessRuleViolationError, ResourceNotFoundError) as exc:
            return {"can_enroll": False, "reason": exc.message, "is_retake": is_retake, "already_enrolled": already}

    async def withdraw(self, enrollment_id: uuid.UUID, *, student_id: uuid.UUID | None, actor_id: uuid.UUID, reason: str = "REQUEST",
                       keep_future_fees: bool = False) -> Enrollment:
        """student_id is provided for self-service withdrawal (ownership
        check); None for an admin-initiated withdrawal (no ownership
        restriction)."""
        enrollment = await self.enrollment_repo.get_by_id(enrollment_id)
        if enrollment is None or (student_id is not None and enrollment.student_id != student_id):
            raise ResourceNotFoundError("Enrollment not found.")
        if enrollment.status != EnrollmentStatus.ENROLLED:
            raise BusinessRuleViolationError(f"This enrollment is already {enrollment.status.value.lower()}.")

        if reason == "REQUEST":
            from app.core.clock import institute_today

            offering = await self.offering_repo.get_by_id(enrollment.course_offering_id)
            start = offering.term_start_date if offering else None
            if start is not None and (institute_today() - start).days > int(settings.withdrawal_deadline_days):
                if student_id is not None:
                    raise BusinessRuleViolationError(
                        f"The withdrawal deadline ({int(settings.withdrawal_deadline_days)} days after the term started) has passed. Ask the office.")
                reason = "LATE"                      # the office can still withdraw; it shows as W

        enrollment.status = EnrollmentStatus.WITHDRAWN
        enrollment.withdrawal_reason = reason
        enrollment.withdrawn_at = datetime.now(timezone.utc)
        await self.audit.log(
            actor_user_id=actor_id, action="enrollment.withdrawn", entity_type="enrollment", entity_id=enrollment.id,
            metadata={"reason": reason},
        )
        if not keep_future_fees and reason != "ATTENDANCE":
            # This course's own fee: installments not yet due are cancelled (owner decision 6). A student struck off for
            # attendance shortage did not leave by choice: that course's fee stays as it is.
            from app.fees.status_rules import cancel_course_fee_after_withdrawal

            await cancel_course_fee_after_withdrawal(self.session, enrollment, actor_id=actor_id)
        return enrollment

    async def set_outcome(self, enrollment_id: uuid.UUID, *, outcome: EnrollmentOutcome, actor_id: uuid.UUID) -> Enrollment:
        """Admin-only placeholder ahead of a real Exams/Grading module —
        marks the enrollment COMPLETED with a final Pass/Fail outcome."""
        enrollment = await self.enrollment_repo.get_by_id(enrollment_id)
        if enrollment is None:
            raise ResourceNotFoundError("Enrollment not found.")
        if enrollment.status == EnrollmentStatus.WITHDRAWN:
            raise BusinessRuleViolationError("Can't record an outcome for a withdrawn enrollment.")
        from app.academics.models import EnrollmentResult

        frozen = (await self.session.execute(select(EnrollmentResult.id).where(EnrollmentResult.enrollment_id == enrollment.id))).first()
        if frozen is not None:
            raise BusinessRuleViolationError("This result is already published with a grade. Correct the marks instead "
                                             "(Result sheets \u2192 correction), so the grade and pass/fail stay the same everywhere.")

        enrollment.status = EnrollmentStatus.COMPLETED
        enrollment.outcome = outcome
        await self.audit.log(
            actor_user_id=actor_id, action="enrollment.outcome_set", entity_type="enrollment", entity_id=enrollment.id,
            after={"outcome": outcome.value},
        )
        return enrollment

    async def other_sections(self, enrollment_id: uuid.UUID) -> list[dict]:
        enrollment = await self.enrollment_repo.get_by_id(enrollment_id)
        if enrollment is None:
            raise ResourceNotFoundError("Enrollment not found.")
        here = await self.offering_repo.get_by_id(enrollment.course_offering_id)
        out = []
        for o in await self.offering_repo.list_by_term(here.term_label):
            if o.course_id != here.course_id or o.id == here.id or o.attendance_locked_at is not None:
                continue
            seats = None if o.capacity is None else max(0, o.capacity - await self.offering_repo.enrolled_count(o.id))
            out.append({"id": o.id, "section_label": o.section_label, "seats_left": seats})
        return out

    async def move_section(self, enrollment_id: uuid.UUID, target_offering_id: uuid.UUID, *, actor_id: uuid.UUID) -> Enrollment:
        """Move a student to another section of the same course in the same term (timetable, numbers).
        Only before anything is recorded for them in this section: once attendance or marks exist,
        withdraw and enrol instead so nothing is silently moved or lost."""
        from app.academics.models import CourseOffering as _CO
        from app.academics.models import StudentMark
        from app.attendance.models import AttendanceRecord

        enrollment = await self.enrollment_repo.get_by_id(enrollment_id)
        if enrollment is None or enrollment.status != EnrollmentStatus.ENROLLED:
            raise BusinessRuleViolationError("Only a current enrollment can be moved.")
        here = await self.offering_repo.get_by_id(enrollment.course_offering_id)
        target = await self.offering_repo.get_by_id(target_offering_id)
        if target is None or target.course_id != here.course_id or target.term_label != here.term_label or target.id == here.id:
            raise BusinessRuleViolationError("Choose another section of the same course in the same term.")
        await self._refuse_finished_offering(target)
        marks = (await self.session.execute(select(StudentMark.id).where(StudentMark.enrollment_id == enrollment.id))).first()
        att = (await self.session.execute(select(AttendanceRecord.id).join(ClassSession, ClassSession.id == AttendanceRecord.class_session_id)
                                          .where(AttendanceRecord.student_id == enrollment.student_id, ClassSession.course_offering_id == here.id))).first()
        if marks or att:
            raise BusinessRuleViolationError("Attendance or marks are already recorded for this student in this section, so it cannot simply be moved. "
                                             "Withdraw them here and enrol them in the other section instead.")
        if await self.enrollment_repo.get_by_student_and_offering(enrollment.student_id, target.id) is not None:
            raise BusinessRuleViolationError("The student already has a record in that section.")
        if target.capacity is not None:
            await self.session.execute(select(_CO.id).where(_CO.id == target.id).with_for_update())
            if await self.offering_repo.enrolled_count(target.id) >= target.capacity:
                raise BusinessRuleViolationError(f"Section {target.section_label} is full.")
        clash = await TimetableService(self.session).student_clash(enrollment.student_id, target, ignore_enrollment_id=enrollment.id)
        if clash:
            raise BusinessRuleViolationError(clash)
        enrollment.course_offering_id = target.id
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="enrollment.section_moved", entity_type="enrollment", entity_id=enrollment.id,
                             before={"course_offering_id": str(here.id), "section": here.section_label}, after={"course_offering_id": str(target.id), "section": target.section_label})
        return enrollment

    async def list_for_student(self, student_id: uuid.UUID) -> list[Enrollment]:
        return await self.enrollment_repo.list_for_student(student_id)

    async def list_for_offering(self, course_offering_id: uuid.UUID) -> list[Enrollment]:
        return await self.enrollment_repo.list_for_offering(course_offering_id)

    async def _offerings_for_term_or_fail(self, term_label: str) -> list:
        """The offerings of an EXISTING term. A typo ("Fal 2026") used to
        succeed silently with zero offerings; now it is refused, and a
        case/spacing near-match is suggested."""
        offerings = await self.offering_repo.list_by_term(term_label)
        if not offerings:
            known = await self.offering_repo.list_distinct_terms()
            norm = lambda t: "".join(t.lower().split())
            close = [t for t in known if norm(t) == norm(term_label)]
            hint = f' Did you mean "{close[0]}"?' if close else (f" Known terms: {', '.join(known[:6])}." if known else "")
            raise ResourceNotFoundError(f'No course offerings found for term "{term_label}".{hint}')
        return offerings

    async def _end_semester_blockers(self, offerings: list) -> list[str]:
        """Courses that still have students but no locked attendance or no published result."""
        from app.academics.models import ResultSheet, ResultSheetStatus

        ids = [o.id for o in offerings]
        published = set((await self.session.execute(select(ResultSheet.course_offering_id).where(
            ResultSheet.course_offering_id.in_(ids), ResultSheet.status == ResultSheetStatus.PUBLISHED))).scalars()) if ids else set()
        out = []
        for o in offerings:
            enrolled = [e for e in await self.enrollment_repo.list_for_offering(o.id) if e.status == EnrollmentStatus.ENROLLED]
            if not enrolled:
                continue
            missing = []
            if getattr(o, "attendance_locked_at", None) is None:
                missing.append("attendance not locked")
            if o.id not in published:
                missing.append("result not published")
            if missing:
                course = await self.session.get(Course, o.course_id)
                out.append(f"{course.code if course else o.id}{' ' + o.section_label if getattr(o, 'section_label', None) else ''}: {', '.join(missing)}")
        return out

    async def preview_end_semester(self, term_label: str) -> dict:
        """What end_semester() would do, without changing anything (the
        admin UI shows this in its confirmation)."""
        offerings = await self._offerings_for_term_or_fail(term_label)
        blockers = await self._end_semester_blockers(offerings)
        to_complete = ungraded = 0
        for offering in offerings:
            for enrollment in await self.enrollment_repo.list_for_offering(offering.id):
                if enrollment.status == EnrollmentStatus.ENROLLED:
                    to_complete += 1
                    if enrollment.outcome is None:
                        ungraded += 1
        return {"term_label": term_label, "offerings_affected": len(offerings),
                "enrollments_to_complete": to_complete, "ungraded_count": ungraded,
                "not_ready": blockers, "override_needed": bool(blockers and settings.require_results_before_end_semester)}

    async def end_semester(self, term_label: str, *, actor_id: uuid.UUID, override_reason: str | None = None) -> dict:
        """The 'reset enrollment for next semester' action: for every
        offering in this term, close it to new enrollment and finalize
        every still-ENROLLED row to COMPLETED. Outcome (Pass/Fail) is
        left as-is — if an admin already recorded it via set_outcome()
        it's preserved; if not, it stays null (a placeholder ahead of
        a real Exams/Grading module, same as everywhere else this
        project defers grading). This does NOT touch WITHDRAWN rows —
        those are already final and shouldn't be reinterpreted as
        completed. Deliberately does NOT block on ungraded enrollments
        — see preview_end_semester() above, which the UI calls first so
        the admin can see and decide, rather than the backend silently
        blocking a real action on an incomplete feature."""
        offerings = await self._offerings_for_term_or_fail(term_label)
        blockers = await self._end_semester_blockers(offerings)
        override_reason = (override_reason or "").strip() or None
        if blockers and settings.require_results_before_end_semester and (override_reason is None or len(override_reason) < 10):
            raise BusinessRuleViolationError(
                f"{len(blockers)} course(s) are not finished: " + "; ".join(blockers[:8]) + ("; ..." if len(blockers) > 8 else "")
                + ". Lock their attendance and publish their results first, or give a reason (at least 10 letters) to end the semester anyway.",
                details={"not_ready": blockers})
        completed_count = 0
        ungraded_count = 0
        for offering in offerings:
            offering.is_open_for_enrollment = False
            enrollments = await self.enrollment_repo.list_for_offering(offering.id)
            for enrollment in enrollments:
                if enrollment.status == EnrollmentStatus.ENROLLED:
                    if enrollment.outcome is None:
                        ungraded_count += 1
                    enrollment.status = EnrollmentStatus.COMPLETED
                    completed_count += 1

        result = {
            "term_label": term_label,
            "offerings_closed": len(offerings),
            "enrollments_completed": completed_count,
            "ungraded_count": ungraded_count,
        }
        if blockers:
            result["ended_with_unfinished_courses"] = blockers
            result["override_reason"] = override_reason
        await self.audit.log(
            actor_user_id=actor_id, action="enrollment.semester_ended", entity_type="course_offering", entity_id=None,
            metadata=result,
        )
        return result


def _times_overlap(start1: time, end1: time, start2: time, end2: time) -> bool:
    return start1 < end2 and start2 < end1


class PeriodService:
    """Institute-wide daily periods — admin-defined once, the same
    times every day. Both manual slot creation and TimetableService's
    auto-assigner pick a Period instead of typing arbitrary times."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = PeriodRepository(session)
        self.audit = AuditService(session)

    async def list_all(self) -> list[Period]:
        return await self.repo.list_all()

    async def create(self, *, period_number: int, start_time: time, end_time: time, actor_id: uuid.UUID) -> Period:
        if start_time >= end_time:
            raise BusinessRuleViolationError("start_time must be before end_time.")
        if await self.repo.get_by_number(period_number) is not None:
            raise DuplicateResourceError(f"Period {period_number} already exists.")
        for other in await self.repo.list_all():
            if _times_overlap(start_time, end_time, other.start_time, other.end_time):
                raise BusinessRuleViolationError(
                    f"This overlaps with Period {other.period_number} ({other.start_time}\u2013{other.end_time})."
                )

        period = await self.repo.create(Period(period_number=period_number, start_time=start_time, end_time=end_time))
        await self.audit.log(
            actor_user_id=actor_id, action="period.created", entity_type="period", entity_id=period.id,
            after={"period_number": period_number, "start_time": str(start_time), "end_time": str(end_time)},
        )
        return period

    async def delete(self, period_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        period = await self.repo.get_by_id(period_id)
        if period is None:
            raise ResourceNotFoundError("Period not found.")
        await self.repo.delete(period)
        await self.audit.log(actor_user_id=actor_id, action="period.deleted", entity_type="period", entity_id=period_id)


class TimetableService:
    """The recurring weekly pattern side of the timetable — creating a
    slot runs the full conflict check (room, teacher, student-batch —
    see _check_conflicts) against every OTHER active slot in the
    institute, then immediately generates one ClassSession per matching
    weekday between the offering's term_start_date and term_end_date.
    See TimetableSlot's own docstring for the full reasoning."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.slot_repo = TimetableSlotRepository(session)
        self.session_repo = ClassSessionRepository(session)
        self.offering_repo = CourseOfferingRepository(session)
        self.course_program_repo = CourseProgramRepository(session)
        self.period_repo = PeriodRepository(session)
        # Remembered for the life of this request: the same course links / enrolment lists / weekday
        # slot lists are looked up many times while checking a timetable, and never change mid-request
        # (the weekday slot list is refreshed whenever a slot is created or deleted).
        self._links_memo: dict = {}
        self._enrolled_memo: dict = {}
        self._day_memo: dict = {}
        self._offering_memo: dict = {}
        self.audit = AuditService(session)

    @staticmethod
    def _ranges_overlap(a: CourseOffering, b: CourseOffering) -> bool:
        """Do two offerings run in overlapping date ranges? (Fall 2026 and
        Spring 2027 never compete for the same room/teacher.) Unknown dates
        are treated as overlapping -- the safe assumption."""
        if None in (a.term_start_date, a.term_end_date, b.term_start_date, b.term_end_date):
            return True
        return a.term_start_date <= b.term_end_date and b.term_start_date <= a.term_end_date

    async def _links_for(self, course_id: uuid.UUID) -> set:
        if course_id not in self._links_memo:
            self._links_memo[course_id] = {
                (l.program_id, l.semester_number) for l in await self.course_program_repo.list_for_course(course_id)
            }
        return self._links_memo[course_id]

    async def _enrolled_for(self, offering_id: uuid.UUID) -> set:
        if offering_id not in self._enrolled_memo:
            self._enrolled_memo[offering_id] = await self.offering_repo.enrolled_student_ids(offering_id)
        return self._enrolled_memo[offering_id]

    async def _slots_on(self, day_of_week: DayOfWeek) -> list:
        if day_of_week not in self._day_memo:
            self._day_memo[day_of_week] = list(await self.slot_repo.list_for_day(day_of_week))
        return self._day_memo[day_of_week]

    async def _offering(self, offering_id: uuid.UUID):
        if offering_id not in self._offering_memo:
            self._offering_memo[offering_id] = await self.offering_repo.get_by_id(offering_id)
        return self._offering_memo[offering_id]

    async def _live_neighbours(
        self, offering: CourseOffering, day_of_week: DayOfWeek, start_time: time, end_time: time,
        exclude_slot_id: uuid.UUID | None = None,
    ) -> list[tuple[TimetableSlot, CourseOffering]]:
        """Every OTHER slot on this weekday, at an overlapping time, belonging to
        an offering that runs at the same time of year."""
        out = []
        for slot in await self._slots_on(day_of_week):
            if slot.id == exclude_slot_id:
                continue
            if not _times_overlap(start_time, end_time, slot.start_time, slot.end_time):
                continue
            other = await self._offering(slot.course_offering_id)
            if other is None or not self._ranges_overlap(offering, other):
                continue
            out.append((slot, other))
        return out

    async def _people_conflict(self, offering: CourseOffering, neighbours: list, when: str) -> str | None:
        """Everything except the room: teacher, student cohort, actual enrolled students.
        Returns the reason it cannot be placed here, or None if it can."""
        # 2. Teacher -- one person cannot teach two classes at once.
        if offering.assigned_teacher_id is not None:
            for slot, other in neighbours:
                if other.id != offering.id and other.assigned_teacher_id == offering.assigned_teacher_id:
                    return f"This offering's teacher already has a class on {when.format(slot.start_time, slot.end_time)}."

        # 3. Student cohort -- the SAME section of the same program+semester cannot attend two
        # different courses at once. Different sections (A vs B, Morning vs Evening) are different
        # students, and other sections of the SAME course are alternatives, so neither conflicts.
        batch_keys = await self._links_for(offering.course_id)
        if batch_keys:
            for slot, other in neighbours:
                if other.id == offering.id or other.course_id == offering.course_id:
                    continue
                if other.section_label.strip().lower() != offering.section_label.strip().lower():
                    continue
                if batch_keys & await self._links_for(other.course_id):
                    return (
                        f"Students in this program/semester (section {offering.section_label}) already have a class "
                        f"scheduled on {when.format(slot.start_time, slot.end_time)}."
                    )

        # 4. Actual enrolled students -- if even ONE student is enrolled in both offerings they
        # cannot attend both, whatever the sections say.
        my_students = await self._enrolled_for(offering.id)
        if my_students:
            for slot, other in neighbours:
                if other.id == offering.id:
                    continue
                shared = my_students & await self._enrolled_for(other.id)
                if shared:
                    return f"{len(shared)} enrolled student(s) already have a class on {when.format(slot.start_time, slot.end_time)}."
        return None

    async def _check_conflicts(
        self, *, course_offering_id: uuid.UUID, day_of_week: DayOfWeek, start_time: time, end_time: time,
        room: str, exclude_slot_id: uuid.UUID | None = None,
    ) -> None:
        offering = await self._offering(course_offering_id)
        neighbours = await self._live_neighbours(offering, day_of_week, start_time, end_time, exclude_slot_id)
        when = f"{day_of_week.value} {{}}\u2013{{}}"

        # 1. Room -- a physical resource, whoever owns the booking.
        for slot, _other in neighbours:
            if "".join((slot.room or "").split()).lower() == "".join((room or "").split()).lower():     # "Lab 1" and "lab1" are the same room
                raise BusinessRuleViolationError(
                    f"Room '{room}' is already booked on {when.format(slot.start_time, slot.end_time)}."
                )
        problem = await self._people_conflict(offering, neighbours, when)
        if problem:
            raise BusinessRuleViolationError(problem)

    async def offering_repo_enrolled(self, offering_id: uuid.UUID) -> set[uuid.UUID]:
        return await self._enrolled_for(offering_id)

    async def auto_schedule_term(
        self, *, term_label: str, rooms: list[str], sessions_per_week: int | None, actor_id: uuid.UUID
    ) -> dict:
        """Timetable a WHOLE term in one action: every offering that has no
        weekly slots yet gets auto-scheduled, trying each supplied room in turn
        until one is free at a conflict-free time. Classes per week default to
        the course's credit hours. Each offering is independent -- one that
        cannot be placed is reported with the reason and never blocks the rest."""
        rooms = [r.strip() for r in rooms if r and r.strip()]
        if not rooms:
            raise BusinessRuleViolationError("Give at least one room to schedule into.")
        term = await self.offering_repo.canonical_term_label(term_label)
        offerings = await self.offering_repo.list_by_term(term)
        if not offerings:
            raise ResourceNotFoundError(f"No course offerings exist for {term}.")
        periods = await self.period_repo.list_all()
        if not periods:
            raise BusinessRuleViolationError("No periods have been defined yet — define at least one Period first.")
        scheduled, skipped, failed = [], [], []
        rows = []
        for o in offerings:
            course = await self.session.get(Course, o.course_id)
            rows.append((course.code if course else "?", o.section_label, o, course))
        for code, section, o, course in sorted(rows, key=lambda r: (r[0], r[1])):
            if await self.slot_repo.list_for_offering(o.id):
                skipped.append({"course_code": code, "section": section, "reason": "already has a timetable"})
                continue
            per_week = sessions_per_week or max(1, min(5, course.credit_hours if course else 3))
            placement = await self._find_placement(o, per_week, rooms, periods)
            if placement is None:
                failed.append({"course_code": code, "section": section,
                               "reason": f"No {per_week}-day slot was free (teacher, students and a room) at any period. "
                                         "Add rooms or periods, or reduce classes per week."})
                continue
            period, day_rooms = placement
            try:
                async with self.session.begin_nested():
                    generated = 0
                    for day, chosen_room in day_rooms.items():
                        _slot, count = await self.create_slot(
                            course_offering_id=o.id, period_id=period.id, day_of_week=day, room=chosen_room, actor_id=actor_id
                        )
                        generated += count
                scheduled.append({"course_code": code, "section": section, "room": ", ".join(sorted(set(day_rooms.values()))),
                                  "classes_per_week": per_week, "sessions_generated": generated})
            except BusinessRuleViolationError as exc:
                self._day_memo.clear()          # the rolled-back slots must not linger in the remembered lists
                failed.append({"course_code": code, "section": section, "reason": exc.message})
        await self.audit.log(
            actor_user_id=actor_id, action="timetable.term_auto_scheduled", entity_type="course_offering", entity_id=None,
            metadata={"term": term, "scheduled": len(scheduled), "skipped": len(skipped), "failed": len(failed)},
        )
        return {"scheduled": scheduled, "skipped": skipped, "failed": failed}

    async def copy_from_term(self, *, source_term: str, target_term: str, actor_id: uuid.UUID) -> dict:
        """Repeat last term's timetable: for each offering of the target term
        that has no slots, copy the weekly slots (day, period, room) of the SAME
        course and section from the source term. Every copied slot goes through
        the normal conflict checks; anything that cannot be copied is reported."""
        source = await self.offering_repo.canonical_term_label(source_term)
        target = await self.offering_repo.canonical_term_label(target_term)
        if source.lower() == target.lower():
            raise BusinessRuleViolationError("Choose two different terms.")
        source_offerings = await self.offering_repo.list_by_term(source)
        if not source_offerings:
            raise ResourceNotFoundError(f"No course offerings exist for {source}.")
        by_key = {(o.course_id, o.section_label.strip().lower()): o for o in source_offerings}
        targets = await self.offering_repo.list_by_term(target)
        if not targets:
            raise ResourceNotFoundError(f"No course offerings exist for {target}. Open the semester first.")
        scheduled, skipped, failed = [], [], []
        for o in targets:
            course = await self.session.get(Course, o.course_id)
            code = course.code if course else "?"
            if await self.slot_repo.list_for_offering(o.id):
                skipped.append({"course_code": code, "section": o.section_label, "reason": "already has a timetable"})
                continue
            src = by_key.get((o.course_id, o.section_label.strip().lower()))
            if src is None:
                failed.append({"course_code": code, "section": o.section_label, "reason": f"not offered in {source}"})
                continue
            src_slots = await self.slot_repo.list_for_offering(src.id)
            if not src_slots:
                failed.append({"course_code": code, "section": o.section_label, "reason": f"had no timetable in {source}"})
                continue
            copied, problems = 0, []
            for sl in src_slots:
                try:
                    async with self.session.begin_nested():
                        await self.create_slot(course_offering_id=o.id, period_id=sl.period_id,
                                               day_of_week=sl.day_of_week, room=sl.room, actor_id=actor_id)
                    copied += 1
                except (BusinessRuleViolationError, ResourceNotFoundError) as exc:
                    problems.append(f"{sl.day_of_week.value}: {exc.message}")
            if copied and not problems:
                scheduled.append({"course_code": code, "section": o.section_label, "slots_copied": copied})
            elif copied:
                failed.append({"course_code": code, "section": o.section_label,
                               "reason": f"copied {copied} of {len(src_slots)} slots; " + "; ".join(problems)[:200]})
            else:
                failed.append({"course_code": code, "section": o.section_label, "reason": "; ".join(problems)[:240]})
        await self.audit.log(
            actor_user_id=actor_id, action="timetable.copied_from_term", entity_type="course_offering", entity_id=None,
            metadata={"from": source, "to": target, "scheduled": len(scheduled), "failed": len(failed)},
        )
        return {"scheduled": scheduled, "skipped": skipped, "failed": failed}

    async def assert_teacher_free_for_offering(self, offering: CourseOffering, teacher_id: uuid.UUID) -> None:
        """Before (re)assigning a teacher to an offering that already has
        timetable slots: none of those slots may overlap the teacher's other
        classes."""
        for slot in await self.slot_repo.list_for_offering(offering.id):
            for other_slot, other in await self._live_neighbours(offering, slot.day_of_week, slot.start_time, slot.end_time, slot.id):
                if other.id != offering.id and other.assigned_teacher_id == teacher_id:
                    raise BusinessRuleViolationError(
                        f"That teacher already teaches on {slot.day_of_week.value} "
                        f"{other_slot.start_time}\u2013{other_slot.end_time}; it clashes with this offering's "
                        f"{slot.start_time}\u2013{slot.end_time} slot."
                    )

    async def student_clash(
        self, student_id: uuid.UUID, offering: CourseOffering, ignore_enrollment_id: uuid.UUID | None = None
    ) -> str | None:
        """A message if enrolling this student in `offering` would put them in
        two classes at the same time, else None."""
        my_slots = await self.slot_repo.list_for_offering(offering.id)
        if not my_slots:
            return None
        for enrollment in await EnrollmentRepository(self.session).list_for_student(student_id):
            if enrollment.status != EnrollmentStatus.ENROLLED or enrollment.id == ignore_enrollment_id:
                continue
            other = await self.offering_repo.get_by_id(enrollment.course_offering_id)
            if other is None or other.id == offering.id or not self._ranges_overlap(offering, other):
                continue
            for mine in my_slots:
                for theirs in await self.slot_repo.list_for_offering(other.id):
                    if mine.day_of_week == theirs.day_of_week and _times_overlap(
                        mine.start_time, mine.end_time, theirs.start_time, theirs.end_time
                    ):
                        other_course = await self.session.get(Course, other.course_id)
                        return (
                            f"Timetable clash: {mine.day_of_week.value} {mine.start_time}\u2013{mine.end_time} "
                            f"overlaps '{other_course.code if other_course else 'another course'}' which you are already enrolled in."
                        )
        return None

    async def _generate_sessions_for_slot(self, slot: TimetableSlot, offering: CourseOffering) -> int:
        from app.calendar.service import student_holidays

        holidays = await student_holidays(self.session, offering.term_start_date, offering.term_end_date)   # from the academic calendar
        count = 0
        # A removed slot leaves its sessions behind (detached, cancelled), so
        # re-creating the same day/period must adopt them, not insert twins.
        from sqlalchemy import select

        existing = {
            row.session_date: row
            for row in (await self.session.execute(
                select(ClassSession).where(
                    ClassSession.course_offering_id == offering.id,
                    ClassSession.start_time == slot.start_time,
                    ClassSession.session_date >= offering.term_start_date,
                    ClassSession.session_date <= offering.term_end_date,
                )
            )).scalars().all()
        }
        current = offering.term_start_date
        while current <= offering.term_end_date:
            if DAY_OF_WEEK_BY_PYTHON_WEEKDAY[current.weekday()] == slot.day_of_week:
                off = holidays.get(current)
                prior = existing.get(current)
                if prior is not None:
                    if prior.timetable_slot_id is None:
                        prior.timetable_slot_id = slot.id
                        prior.end_time = slot.end_time
                        prior.room = slot.room
                        if not off and prior.status == ClassSessionStatus.CANCELLED \
                                and prior.cancelled_reason == "Timetable slot removed":
                            prior.status = ClassSessionStatus.SCHEDULED
                            prior.cancelled_reason = None
                        count += 1
                    current += timedelta(days=1)
                    continue
                await self.session_repo.create(
                    ClassSession(
                        course_offering_id=offering.id, timetable_slot_id=slot.id, session_date=current,
                        start_time=slot.start_time, end_time=slot.end_time, room=slot.room,
                        status=ClassSessionStatus.CANCELLED if off else ClassSessionStatus.SCHEDULED,
                        cancelled_reason=f"Holiday: {off}" if off else None,
                    )
                )
                count += 1
            current += timedelta(days=1)
        return count

    async def create_slot(
        self, *, course_offering_id: uuid.UUID, period_id: uuid.UUID, day_of_week: DayOfWeek,
        room: str, actor_id: uuid.UUID,
    ) -> tuple[TimetableSlot, int]:
        offering = await self.offering_repo.get_by_id(course_offering_id)
        if offering is None:
            raise ResourceNotFoundError("Course offering not found.")
        if offering.term_start_date is None or offering.term_end_date is None:
            raise BusinessRuleViolationError(
                "Set this offering's term start/end dates before creating a timetable slot."
            )
        if offering.term_start_date > offering.term_end_date:
            raise BusinessRuleViolationError("term_start_date must be before term_end_date.")

        period = await self.period_repo.get_by_id(period_id)
        if period is None:
            raise ResourceNotFoundError("Period not found.")
        start_time, end_time = period.start_time, period.end_time

        await self._check_conflicts(
            course_offering_id=course_offering_id, day_of_week=day_of_week,
            start_time=start_time, end_time=end_time, room=room,
        )

        slot = TimetableSlot(
            course_offering_id=course_offering_id, period_id=period_id, day_of_week=day_of_week,
            start_time=start_time, end_time=end_time, room=room, created_by=actor_id,
        )
        await self.slot_repo.create(slot)
        self._day_memo.pop(day_of_week, None)
        generated_count = await self._generate_sessions_for_slot(slot, offering)

        await self.audit.log(
            actor_user_id=actor_id, action="timetable_slot.created", entity_type="timetable_slot", entity_id=slot.id,
            after={
                "course_offering_id": str(course_offering_id), "period_number": period.period_number,
                "day_of_week": day_of_week.value, "start_time": str(start_time), "end_time": str(end_time),
                "room": room, "sessions_generated": generated_count,
            },
        )
        return slot, generated_count

    async def _find_placement(self, offering: CourseOffering, per_week: int, rooms: list[str], periods: list):
        """The greedy search shared by "auto-assign one course" and "auto-schedule the whole term".

        For each period in order: which weekdays are free of teacher / student clashes AND have at
        least one free room? The first period with enough such days wins, and the classes are spread
        across the week. Working out the people-clashes ONCE per (period, day) -- and only then
        looking at rooms -- keeps this fast even with hundreds of courses and dozens of rooms.
        Returns (period, {weekday: room}) or None."""
        weekdays = [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, DayOfWeek.THURSDAY, DayOfWeek.FRIDAY]
        teacher_busy_days: set[DayOfWeek] = set()
        if offering.assigned_teacher_id is not None:
            teacher_busy_days = {sl.day_of_week for sl in await self.slot_repo.list_for_teacher(offering.assigned_teacher_id)}
        for period in periods:
            usable: dict[DayOfWeek, list[str]] = {}
            for day in weekdays:
                neighbours = await self._live_neighbours(offering, day, period.start_time, period.end_time)
                when = f"{day.value} {{}}\u2013{{}}"
                if await self._people_conflict(offering, neighbours, when) is not None:
                    continue
                taken = {sl.room for sl, _o in neighbours}
                free = [r for r in rooms if r not in taken]
                if free:
                    usable[day] = free
            if len(usable) < per_week:
                continue
            chosen = self._select_spread_days([d for d in weekdays if d in usable], per_week, teacher_busy_days)
            common = [r for r in rooms if all(r in usable[d] for d in chosen)]      # one room all week, if possible
            return period, {d: (common[0] if common else usable[d][0]) for d in chosen}
        return None

    async def auto_assign(
        self, *, course_offering_id: uuid.UUID, sessions_per_week: int, room: str, actor_id: uuid.UUID,
    ) -> tuple[list[TimetableSlot], int]:
        """A simple greedy auto-scheduler, not a full optimizer — see
        the conversation this was scoped in. Tries each defined Period
        in order (lowest period_number first); the first Period with
        at least `sessions_per_week` conflict-free weekdays (Mon-Fri)
        wins, and every session for this course lands on that SAME
        period (one consistent time, e.g. 'always 9am') rather than
        mixing periods across days. Among the conflict-free weekdays,
        picks the subset that spreads the sessions most evenly across
        the week (e.g. Mon/Wed/Fri over three days in a row) — see
        _select_spread_days for the actual tiebreak logic."""
        if sessions_per_week < 1 or sessions_per_week > 5:
            raise BusinessRuleViolationError("sessions_per_week must be between 1 and 5 (Monday through Friday).")

        offering = await self.offering_repo.get_by_id(course_offering_id)
        if offering is None:
            raise ResourceNotFoundError("Course offering not found.")
        if offering.term_start_date is None or offering.term_end_date is None:
            raise BusinessRuleViolationError(
                "Set this offering's term start/end dates before auto-assigning a timetable."
            )

        periods = await self.period_repo.list_all()
        if not periods:
            raise BusinessRuleViolationError("No periods have been defined yet — define at least one Period first.")

        placement = await self._find_placement(offering, sessions_per_week, [room], periods)
        if placement is None:
            raise BusinessRuleViolationError(
                f"Could not find {sessions_per_week} conflict-free weekday(s) in room '{room}' at any single "
                "period. Try a different room, add more periods, or reduce sessions per week."
            )
        chosen_period, day_rooms = placement
        chosen_days = list(day_rooms)

        created_slots = []
        total_sessions = 0
        for day in chosen_days:
            slot, count = await self.create_slot(
                course_offering_id=course_offering_id, period_id=chosen_period.id, day_of_week=day,
                room=day_rooms[day], actor_id=actor_id,
            )
            created_slots.append(slot)
            total_sessions += count

        await self.audit.log(
            actor_user_id=actor_id, action="timetable.auto_assigned", entity_type="course_offering",
            entity_id=course_offering_id,
            after={
                "period_number": chosen_period.period_number, "days": [d.value for d in chosen_days],
                "sessions_generated": total_sessions,
            },
        )
        return created_slots, total_sessions

    def _select_spread_days(
        self, available: list[DayOfWeek], n: int, teacher_busy_days: set[DayOfWeek],
    ) -> list[DayOfWeek]:
        """Picks n days out of `available` that spread as evenly as
        possible across the week — maximizes the smallest gap between
        consecutive chosen days, so a 3x/week course lands on
        Mon/Wed/Fri rather than three days running. Ties broken by
        preferring the combination that reuses more of the teacher's
        already-busy days, per the approved decision: course spacing
        is the priority, minimizing the teacher's distinct days worked
        is the tiebreak, not the other way around."""
        day_order = [DayOfWeek.MONDAY, DayOfWeek.TUESDAY, DayOfWeek.WEDNESDAY, DayOfWeek.THURSDAY, DayOfWeek.FRIDAY]
        if n >= len(available):
            return list(available)

        indices = sorted(day_order.index(d) for d in available)
        best_combo = None
        best_score = None
        for combo in itertools.combinations(indices, n):
            min_gap = min((combo[i + 1] - combo[i] for i in range(len(combo) - 1)), default=0)
            teacher_overlap = len({day_order[i] for i in combo} & teacher_busy_days)
            score = (min_gap, teacher_overlap)
            if best_score is None or score > best_score:
                best_score = score
                best_combo = combo

        return [day_order[i] for i in best_combo]

    async def list_slots(self, course_offering_id: uuid.UUID) -> list[TimetableSlot]:
        return await self.slot_repo.list_for_offering(course_offering_id)

    async def delete_slot(self, slot_id: uuid.UUID, *, actor_id: uuid.UUID) -> int:
        """Cancels this slot's not-yet-occurred SCHEDULED sessions
        (today or later), then deletes the slot itself — past sessions,
        and their attendance/content history, are untouched (ON DELETE
        SET NULL just detaches their timetable_slot_id)."""
        slot = await self.slot_repo.get_by_id(slot_id)
        if slot is None:
            raise ResourceNotFoundError("Timetable slot not found.")

        today = institute_today()
        future_sessions = await self.session_repo.list_scheduled_from_slot_on_or_after(slot_id, today)
        for class_session in future_sessions:
            class_session.status = ClassSessionStatus.CANCELLED
            class_session.cancelled_reason = "Timetable slot removed"

        # FUTURE sessions that never had any attendance, content, resources or correction requests are just empty
        # calendar rows: remove them. Past ones stay (audit ACAD-01): a past class with no attendance is a gap that must
        # still be marked or cancelled, and deleting it would hide the gap and change everyone's attendance %.
        from sqlalchemy import text

        await self.session.execute(
            text(
                "DELETE FROM class_sessions cs WHERE cs.timetable_slot_id = :slot AND cs.session_date >= :today "
                "AND cs.attendance_taken_at IS NULL AND cs.content_locked_at IS NULL "
                "AND cs.topic_completed = false AND cs.title IS NULL AND cs.description IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM attendance_records a WHERE a.class_session_id = cs.id) "
                "AND NOT EXISTS (SELECT 1 FROM attendance_correction_requests r WHERE r.class_session_id = cs.id) "
                "AND NOT EXISTS (SELECT 1 FROM class_session_resources x WHERE x.class_session_id = cs.id)"
            ),
            {"slot": slot_id, "today": today},
        )

        # Detach ALL of this slot's sessions (past ones keep their
        # attendance/content history, just no longer point at a slot that
        # is about to stop existing). Explicit rather than relying only on
        # the FK's ON DELETE SET NULL so ORM-cached rows stay consistent.
        from sqlalchemy import update

        await self.session.execute(
            update(ClassSession)
            .where(ClassSession.timetable_slot_id == slot_id)
            .values(timetable_slot_id=None)
            .execution_options(synchronize_session="fetch")
        )
        self._day_memo.pop(slot.day_of_week, None)
        await self.slot_repo.delete(slot)
        await self.audit.log(
            actor_user_id=actor_id, action="timetable_slot.deleted", entity_type="timetable_slot", entity_id=slot_id,
            after={"future_sessions_cancelled": len(future_sessions)},
        )
        return len(future_sessions)


class ClassSessionService:
    """The generated, dated occurrence — what attendance attaches to,
    and where per-session content (topic/description/uploaded
    notes-slides) lives. See ClassSession's own docstring."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.session_repo = ClassSessionRepository(session)
        self.resource_repo = ClassSessionResourceRepository(session)
        self.offering_repo = CourseOfferingRepository(session)
        self.audit = AuditService(session)

    async def get(self, class_session_id: uuid.UUID) -> ClassSession:
        class_session = await self.session_repo.get_by_id(class_session_id)
        if class_session is None:
            raise ResourceNotFoundError("Class session not found.")
        return class_session

    async def list_for_offering(self, course_offering_id: uuid.UUID) -> list[ClassSession]:
        return await self.session_repo.list_for_offering(course_offering_id)

    async def update(
        self, class_session_id: uuid.UUID, *, session_date: date | None = None, start_time: time | None = None,
        end_time: time | None = None, room: str | None = None, title: str | None = ..., description: str | None = ...,
        actor_id: uuid.UUID,
    ) -> ClassSession:
        """Per-session override — edits THIS one occurrence only. Never
        touches the generating TimetableSlot or any other session it
        produced (e.g. moving one class to a different room for a
        one-off reason doesn't move every future occurrence)."""
        class_session = await self.get(class_session_id)
        if class_session.status == ClassSessionStatus.CANCELLED:
            raise BusinessRuleViolationError("Cannot edit a cancelled session.")
        before = {
            "session_date": str(class_session.session_date), "start_time": str(class_session.start_time),
            "end_time": str(class_session.end_time), "room": class_session.room,
        }

        if session_date is not None:
            class_session.session_date = session_date
        if start_time is not None:
            class_session.start_time = start_time
        if end_time is not None:
            class_session.end_time = end_time
        if class_session.start_time >= class_session.end_time:
            raise BusinessRuleViolationError("start_time must be before end_time.")
        if room is not None:
            class_session.room = room
        if title is not ...:
            class_session.title = title
        if description is not ...:
            class_session.description = description

        await self.audit.log(
            actor_user_id=actor_id, action="class_session.updated", entity_type="class_session",
            entity_id=class_session.id, before=before,
            after={
                "session_date": str(class_session.session_date), "start_time": str(class_session.start_time),
                "end_time": str(class_session.end_time), "room": class_session.room,
            },
        )
        return class_session

    async def cancel(self, class_session_id: uuid.UUID, *, reason: str, actor_id: uuid.UUID) -> ClassSession:
        class_session = await self.get(class_session_id)
        if class_session.status == ClassSessionStatus.CANCELLED:
            raise BusinessRuleViolationError("This session is already cancelled.")
        class_session.status = ClassSessionStatus.CANCELLED
        class_session.cancelled_reason = reason
        await self.audit.log(
            actor_user_id=actor_id, action="class_session.cancelled", entity_type="class_session",
            entity_id=class_session.id, after={"reason": reason},
        )
        return class_session

    async def set_titles(
        self, offering_id: uuid.UUID, items: list[tuple[uuid.UUID, str | None]], *, actor_id: uuid.UUID,
        skip_locked: bool = False,
    ) -> tuple[int, int]:
        """Save the topic of many sessions of ONE offering at once (a course
        plan). Every session must belong to this offering; cancelled sessions
        are skipped, and -- for a teacher (skip_locked) -- so are classes the
        admin has locked. Returns (updated, skipped_because_locked)."""
        updated = skipped_locked = 0
        for session_id, title in items:
            class_session = await self.get(session_id)
            if class_session.course_offering_id != offering_id:
                raise BusinessRuleViolationError("One of the sessions does not belong to this course offering.")
            if class_session.status == ClassSessionStatus.CANCELLED:
                continue
            if skip_locked and class_session.content_locked_at is not None:
                skipped_locked += 1
                continue
            cleaned = (title or "").strip() or None
            if class_session.title != cleaned:
                class_session.title = cleaned
                updated += 1
        await self.audit.log(
            actor_user_id=actor_id, action="class_session.lecture_plan_saved", entity_type="course_offering",
            entity_id=offering_id, metadata={"sessions_updated": updated, "skipped_locked": skipped_locked},
        )
        return updated, skipped_locked

    async def set_content_lock(self, class_session_id: uuid.UUID, *, locked: bool, actor_id: uuid.UUID) -> ClassSession:
        """Lock or unlock ONE class's content (admin)."""
        class_session = await self.get(class_session_id)
        class_session.content_locked_at = datetime.now(timezone.utc) if locked else None
        await self.audit.log(
            actor_user_id=actor_id, action="class_session.content_locked" if locked else "class_session.content_unlocked",
            entity_type="class_session", entity_id=class_session_id,
        )
        return class_session

    async def cancel_day(
        self, session_date: date, *, reason: str, term_label: str | None, actor_id: uuid.UUID
    ) -> dict:
        """Public holiday / strike / weather: cancel EVERY still-scheduled
        class on this date in one action instead of one by one. Classes whose
        attendance has already been taken are left alone (cancel those
        individually if truly needed) and reported back."""
        stmt = (
            select(ClassSession)
            .join(CourseOffering, ClassSession.course_offering_id == CourseOffering.id)
            .where(ClassSession.session_date == session_date, ClassSession.status == ClassSessionStatus.SCHEDULED)
        )
        if term_label:
            stmt = stmt.where(
                CourseOffering.term_label == await CourseOfferingRepository(self.session).canonical_term_label(term_label)
            )
        cancelled = skipped = 0
        for class_session in (await self.session.execute(stmt)).scalars().all():
            if class_session.attendance_taken_at is not None:
                skipped += 1
                continue
            class_session.status = ClassSessionStatus.CANCELLED
            class_session.cancelled_reason = reason
            cancelled += 1
        await self.audit.log(
            actor_user_id=actor_id, action="class_session.day_cancelled", entity_type="class_session", entity_id=None,
            metadata={"date": str(session_date), "reason": reason, "cancelled": cancelled,
                      "skipped_attendance_taken": skipped, "term": term_label},
        )
        return {"date": str(session_date), "cancelled": cancelled, "skipped_attendance_already_taken": skipped}

    async def add_file_resource(
        self, class_session_id: uuid.UUID, *, resource_type: LectureResourceType, content: bytes,
        original_filename: str, content_type: str, actor_id: uuid.UUID,
    ) -> ClassSessionResource:
        class_session = await self.get(class_session_id)
        try:
            storage_key, safe_name = save_uploaded_file(
                category="class-session-resources", entity_id=class_session.id, content=content,
                original_filename=original_filename, content_type=content_type,
                allowed_content_types=ALLOWED_PDF_CONTENT_TYPES, max_bytes=MAX_CERTIFICATE_PDF_BYTES,
            )
        except ValueError as exc:
            raise BusinessRuleViolationError(str(exc)) from exc

        resource = ClassSessionResource(
            class_session_id=class_session_id, resource_type=resource_type,
            storage_key=storage_key, original_name=safe_name,
        )
        await self.resource_repo.create(resource)
        await self.audit.log(
            actor_user_id=actor_id, action="class_session_resource.uploaded",
            entity_type="class_session_resource", entity_id=resource.id,
        )
        return resource

    async def add_link_resource(
        self, class_session_id: uuid.UUID, *, resource_type: LectureResourceType, external_url: str,
        title: str, actor_id: uuid.UUID,
    ) -> ClassSessionResource:
        """A link (Slides, a video, any external URL) instead of an
        uploaded file — same list, same resource_type categorization,
        just no file behind it. See ClassSessionResource's own
        docstring for why exactly one of storage_key/external_url is
        ever set."""
        await self.get(class_session_id)  # 404s if the session doesn't exist
        if not (external_url.startswith("http://") or external_url.startswith("https://")):
            raise BusinessRuleViolationError("Link must start with http:// or https://.")

        resource = ClassSessionResource(
            class_session_id=class_session_id, resource_type=resource_type,
            external_url=external_url, original_name=title.strip(),
        )
        await self.resource_repo.create(resource)
        await self.audit.log(
            actor_user_id=actor_id, action="class_session_resource.link_added",
            entity_type="class_session_resource", entity_id=resource.id,
        )
        return resource

    async def get_resource(self, resource_id: uuid.UUID) -> ClassSessionResource:
        resource = await self.resource_repo.get_by_id(resource_id)
        if resource is None:
            raise ResourceNotFoundError("Resource not found.")
        return resource

    async def remove_resource(self, resource_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        resource = await self.get_resource(resource_id)
        delete_file(resource.storage_key)
        await self.resource_repo.delete(resource)
        await self.audit.log(
            actor_user_id=actor_id, action="class_session_resource.removed",
            entity_type="class_session_resource", entity_id=resource_id,
        )

    async def list_resources(self, class_session_id: uuid.UUID) -> list[ClassSessionResource]:
        return await self.resource_repo.list_for_session(class_session_id)


class CourseProgressService:
    """How far along an offering actually is versus where it should be
    by now — 'on time, delayed, or missing classes', per the approved
    request. Deliberately keyed off topic_completed (a real signal the
    teacher set), not attendance-taken, since a class can have
    attendance marked without the topic being finished, or vice versa."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.session_repo = ClassSessionRepository(session)

    async def compute_for_offering(self, course_offering_id: uuid.UUID) -> dict:
        sessions = await self.session_repo.list_for_offering(course_offering_id)
        scheduled = [s for s in sessions if s.status == ClassSessionStatus.SCHEDULED]
        today = institute_today()  # institute-local -- see AttendanceService.mark_session's own note on why
        due = [s for s in scheduled if s.session_date <= today]
        completed_count = sum(1 for s in due if s.topic_completed)
        missing_count = len(due) - completed_count

        if not due:
            status = "NOT_STARTED"
        elif missing_count == 0:
            status = "ON_TRACK"
        elif completed_count / len(due) >= 0.8:
            status = "SLIGHTLY_DELAYED"
        else:
            status = "DELAYED"

        return {
            "course_offering_id": course_offering_id,
            "total_sessions": len(scheduled),
            "sessions_due": len(due),
            "sessions_completed": completed_count,
            "sessions_missing": missing_count,
            "completion_percentage": 100.0 if not due else round(completed_count / len(due) * 100, 2),
            "status": status,
        }

class AllocationService:
    """The registrar's 'is this term ready?' view. For one term it lists every
    section with what is still missing (teacher, timetable, dates), each
    teacher's weekly load, and curriculum courses that have NO offering at all
    even though students are sitting in that program/semester."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.offering_repo = CourseOfferingRepository(session)
        self.slot_repo = TimetableSlotRepository(session)

    @staticmethod
    def _hours(slot: TimetableSlot) -> float:
        start = datetime.combine(date.min, slot.start_time)
        end = datetime.combine(date.min, slot.end_time)
        return max((end - start).total_seconds() / 3600.0, 0.0)

    async def overview(self, term_label: str) -> dict:
        term_label = await self.offering_repo.canonical_term_label(term_label)
        offerings = await self.offering_repo.list_by_term(term_label)
        rows, teacher_load = [], {}
        covered_course_ids = set()
        from sqlalchemy import or_

        from app.academics.batch import enrolled_counts, load_by_ids

        # Everything the loop needs, fetched together (one query each) instead of several per section.
        offering_ids = [o.id for o in offerings]
        courses = await load_by_ids(self.session, Course, [o.course_id for o in offerings])
        teachers = await load_by_ids(self.session, Employee, [o.assigned_teacher_id for o in offerings])
        enrolled_by_offering = await enrolled_counts(self.session, offering_ids)
        slots_by_offering: dict = {}
        no_topic_by_offering: dict = {}
        if offering_ids:
            for sl in (await self.session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id.in_(offering_ids)))).scalars():
                slots_by_offering.setdefault(sl.course_offering_id, []).append(sl)
            today = institute_today()
            horizon = today + timedelta(days=14)
            no_topic_by_offering = {
                oid: n for oid, n in (await self.session.execute(
                    select(ClassSession.course_offering_id, func.count()).where(
                        ClassSession.course_offering_id.in_(offering_ids),
                        ClassSession.status == ClassSessionStatus.SCHEDULED,
                        ClassSession.session_date >= today, ClassSession.session_date <= horizon,
                        or_(ClassSession.title.is_(None), func.trim(ClassSession.title) == ""),
                    ).group_by(ClassSession.course_offering_id)
                )).all()
            }
        for o in offerings:
            covered_course_ids.add(o.course_id)
            course = courses.get(o.course_id)
            teacher = teachers.get(o.assigned_teacher_id) if o.assigned_teacher_id else None
            slots = slots_by_offering.get(o.id, [])
            enrolled = enrolled_by_offering.get(o.id, 0)
            weekly_hours = round(sum(self._hours(sl) for sl in slots), 2)
            issues = []
            if teacher is None:
                issues.append("NO_TEACHER")
            if not slots:
                issues.append("NO_TIMETABLE")
            if o.term_start_date is None or o.term_end_date is None:
                issues.append("NO_TERM_DATES")
            if o.capacity is not None and enrolled > o.capacity:
                issues.append("OVER_CAPACITY")
            upcoming_no_topic = no_topic_by_offering.get(o.id, 0)
            if upcoming_no_topic:
                issues.append("TOPICS_MISSING")
            if enrolled == 0:
                issues.append("NO_STUDENTS")
            if teacher is not None:
                load = teacher_load.setdefault(
                    teacher.id, {"teacher_id": str(teacher.id), "teacher_name": f"{teacher.first_name} {teacher.last_name}",
                                 "sections": 0, "weekly_hours": 0.0}
                )
                load["sections"] += 1
                load["weekly_hours"] = round(load["weekly_hours"] + weekly_hours, 2)
            rows.append({
                "offering_id": str(o.id), "course_code": course.code if course else "?",
                "course_title": course.title if course else "?", "section_label": o.section_label,
                "teacher_name": f"{teacher.first_name} {teacher.last_name}" if teacher else None,
                "enrolled": enrolled, "capacity": o.capacity, "slots": len(slots), "weekly_hours": weekly_hours,
                "topics_missing_next_14_days": upcoming_no_topic,
                "is_open_for_enrollment": o.is_open_for_enrollment, "issues": issues,
            })

        # Curriculum courses nobody has opened this term, for cohorts that exist.
        gaps = []
        program_students = {}
        for program_id, semester, count in (
            await self.session.execute(
                select(Student.program_id, Student.current_semester, func.count())
                .where(Student.status == StudentStatus.ACTIVE, Student.program_id.is_not(None), Student.current_semester.is_not(None))
                .group_by(Student.program_id, Student.current_semester)
            )
        ).all():
            program_students[(program_id, semester)] = count
        for (program_id, semester), count in program_students.items():
            program = await self.session.get(Program, program_id)
            for link in await CourseProgramRepository(self.session).list_for_program(program_id):
                if link.semester_number != semester or link.course_id in covered_course_ids:
                    continue
                course = await self.session.get(Course, link.course_id)
                if course is not None and course.is_active:
                    gaps.append({
                        "program_code": program.code if program else "?", "semester": semester,
                        "course_code": course.code, "course_title": course.title, "students_waiting": count,
                    })

        with_issues = sum(1 for r in rows if any(i != "NO_STUDENTS" for i in r["issues"]))
        return {
            "term_label": term_label, "sections": rows, "teacher_loads": sorted(teacher_load.values(), key=lambda t: t["teacher_name"]),
            "courses_without_offering": gaps,
            "summary": {"sections": len(rows), "sections_with_problems": with_issues, "uncovered_courses": len(gaps)},
        }