from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import JSONResponse, Response

from app.academics.terms import AcademicTermService, TermStatus, to_dict
from pydantic import BaseModel, Field
from app.academics.grading_service import AssessmentTypeService, GradingService
from app.academics.models import (
    ResultSheetStatus,
    AssessmentType,
    ClassSessionStatus,
    Course,
    CourseOffering,
    EnrollmentStatus,
    LectureResourceType,
    OfferingAssessment,
)
from app.academics.repository import CourseOfferingRepository, CourseProgramRepository, CourseRepository, EnrollmentRepository
from app.academics.batch import enrolled_counts, load_by_ids
from app.academics.schemas import (
    TimetableFilterOptions,
    WeeklyTimetableOut,
    AddOfferingAssessmentRequest,
    AdminEnrollRequest,
    AutoScheduleTermRequest,
    ClassContentLockRequest,
    ContentLockRequest,
    ContentLockResult,
    BulkTimetableResult,
    CopyTimetableRequest,
    LecturePlanRequest,
    BatchEnrollRequest,
    BatchEnrollResponse,
    GenerateOfferingsRequest,
    GenerateOfferingsResponse,
    AssessmentTypeCreateRequest,
    AssessmentTypeOut,
    AutoAssignRequest,
    AutoAssignResultOut,
    CancelDayRequest,
    CourseOverviewOut,
    OfferingBrowseOut,
    TeacherCourseOverviewOut,
    ClassSessionCancelRequest,
    ClassSessionOut,
    ClassSessionResourceOut,
    ClassSessionUpdateRequest,
    CourseCreateRequest,
    CourseOfferingCreateRequest,
    CourseOfferingOut,
    CourseOfferingUpdateRequest,
    CourseOut,
    CourseProgramLinkRequest,
    CourseProgramOut,
    CourseProgressOut,
    CourseUpdateRequest,
    EndSemesterResponse,
    EnrollmentOut,
    EnrollRequest,
    EnterMarkRequest,
    InstituteTimetableSlotOut,
    LinkResourceCreateRequest,
    OfferingAssessmentOut,
    MyScheduleEntryOut,
    PeriodCreateRequest,
    PeriodOut,
    SetOutcomeRequest,
    SetTopicCompletedRequest,
    StudentMarkDetail,
    StudentMarkOut,
    TeacherPerformanceEntry,
    TeacherProgressEntry,
    TimetableSlotCreatedOut,
    TimetableSlotCreateRequest,
    TimetableSlotOut,
)
from app.academics.calendar_export import build_calendar
from app.academics.weekly_timetable import WeeklyTimetableService, render_pdf
from app.academics.service import AllocationService, ClassSessionService, CourseOfferingService, CourseProgressService, CourseService, EnrollmentService, PeriodService, TimetableService
from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.clock import institute_today
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_permission, require_any_permission
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError
from app.core.storage import SAFE_SERVE_HEADERS, get_file_path
from app.employees.models import Employee
from app.employees.repository import EmployeeRepository
from app.programs.models import Program
from app.students.models import Student
from app.students.repository import StudentRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/academics", tags=["academics"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _resolve_own_student_id(db, current_user: CurrentUser) -> uuid.UUID:
    student = await StudentRepository(db).get_by_user_id(current_user.id)
    if student is None:
        raise AuthorizationError("This account is not linked to a student record.")
    return student.id


async def _resolve_own_teacher_id(db, current_user: CurrentUser) -> uuid.UUID | None:
    employee = await EmployeeRepository(db).get_by_user_id(current_user.id)
    return employee.id if employee else None


async def _can_manage_offering(db, offering: CourseOffering, current_user: CurrentUser) -> bool:
    """ADMIN only, per approved decision — uploading/replacing/removing
    the syllabus and lecture resources is an administrative action now,
    not something the assigned teacher can do (they can only VIEW, via
    _can_view_offering_content below). LECTURE_MANAGE is granted to
    ADMIN alone, so this check is now equivalent to a bare permission
    check — kept as its own function so the one call site that actually
    means 'can edit' stays semantically distinct from 'can view'."""
    return Permissions.LECTURE_MANAGE in current_user.permissions


async def _can_edit_content(db, offering: CourseOffering, current_user: CurrentUser) -> bool:
    """Who may change a course's CONTENT: syllabus, each class's topic, files
    and links. The admin, and the teacher assigned to this offering -- so the
    admin no longer has to collect materials from every teacher and upload
    them. Times, rooms and cancellations are NOT content: they stay with
    _can_manage_offering (admin only)."""
    if Permissions.LECTURE_MANAGE in current_user.permissions:
        return True
    if offering.assigned_teacher_id is None:
        return False
    return offering.assigned_teacher_id == await _resolve_own_teacher_id(db, current_user)


async def _assert_can_edit_content(db, offering: CourseOffering, current_user: CurrentUser, class_session=None) -> None:
    """The admin may always change content. The assigned teacher may too --
    unless the admin has LOCKED the course or this class, in which case they can
    still view everything but change or delete nothing (so old lecture material
    cannot be removed)."""
    if Permissions.LECTURE_MANAGE in current_user.permissions:
        return
    if not await _can_edit_content(db, offering, current_user):
        raise AuthorizationError("Only the teacher assigned to this course (or the admin) can change its content.")
    if offering.content_locked_at is not None:
        raise AuthorizationError("The admin has locked this course's content. You can view it but not change it. Ask the admin to unlock it if it needs updating.")
    if class_session is not None and class_session.content_locked_at is not None:
        raise AuthorizationError("The admin has locked this class's content. You can view it but not change it. Ask the admin to unlock it if it needs updating.")


async def _can_view_offering_content(db, offering: CourseOffering, current_user: CurrentUser) -> bool:
    """Admin/staff (COURSE_VIEW), the offering's assigned teacher
    (view-only — they can see but not edit, see _can_manage_offering),
    or a student actually enrolled in it — this is who gets to see
    lectures, syllabus, and resources for a given offering."""
    if Permissions.COURSE_OFFERING_MANAGE in current_user.permissions or Permissions.ENROLLMENT_MANAGE in current_user.permissions:
        return True
    own_teacher_id = await _resolve_own_teacher_id(db, current_user)
    if own_teacher_id is not None and offering.assigned_teacher_id == own_teacher_id:
        return True
    student = await StudentRepository(db).get_by_user_id(current_user.id)
    if student is not None:
        enrollment = await EnrollmentRepository(db).get_by_student_and_offering(student.id, offering.id)
        if enrollment is not None:
            return True
    return False


async def _can_manage_marks_for_offering(db, offering: CourseOffering, current_user: CurrentUser) -> bool:
    """Same ownership shape as _can_manage_offering-style checks
    elsewhere: ADMIN can manage any offering's grading structure and
    marks; a TEACHER only the offering they're actually assigned to."""
    if Permissions.ENROLLMENT_MANAGE in current_user.permissions or Permissions.RESULT_APPROVE in current_user.permissions:
        return True
    own_teacher_id = await _resolve_own_teacher_id(db, current_user)
    return own_teacher_id is not None and offering.assigned_teacher_id == own_teacher_id


def _to_class_session_out(class_session, resources) -> ClassSessionOut:
    return ClassSessionOut(
        id=class_session.id, course_offering_id=class_session.course_offering_id,
        timetable_slot_id=class_session.timetable_slot_id, session_date=class_session.session_date,
        start_time=class_session.start_time, end_time=class_session.end_time, room=class_session.room,
        status=class_session.status, cancelled_reason=class_session.cancelled_reason,
        title=class_session.title, description=class_session.description,
        topic_completed=class_session.topic_completed, late_marking_note=class_session.late_marking_note,
        content_locked=class_session.content_locked_at is not None,
        resources=[ClassSessionResourceOut.model_validate(r) for r in resources],
    )


async def _curriculum_labels(db, course_ids) -> dict:
    """course_id -> ["BSCS · Semester 3", ...]: the program(s) and semester each course belongs to."""
    from sqlalchemy import select as _select

    from app.academics.models import CourseProgram as _CourseProgram

    ids = {c for c in course_ids if c is not None}
    if not ids:
        return {}
    rows = (await db.execute(
        _select(_CourseProgram.course_id, _CourseProgram.semester_number, Program.code)
        .join(Program, Program.id == _CourseProgram.program_id)
        .where(_CourseProgram.course_id.in_(ids))
        .order_by(Program.code, _CourseProgram.semester_number)
    )).all()
    out: dict = {}
    for course_id, semester, code in rows:
        out.setdefault(course_id, []).append(f"{code} · Semester {semester}")
    return out


async def _to_offering_out(db, offering: CourseOffering) -> CourseOfferingOut:
    # Explicit fetch, not the .course relationship — a freshly created
    # (constructed-then-flushed, never re-queried) offering has no way
    # to trigger even a lazy="selectin" eager load, since the identity
    # map short-circuits any later session.get() for the same object
    # without ever re-issuing a query. db.get() here is always safe:
    # cheap identity-map hit if already loaded, one clean query if not.
    course = await db.get(Course, offering.course_id)
    teacher_name = None
    if offering.assigned_teacher_id is not None:
        teacher = await db.get(Employee, offering.assigned_teacher_id)
        teacher_name = f"{teacher.first_name} {teacher.last_name}" if teacher else None
    enrolled_count = await CourseOfferingRepository(db).enrolled_count(offering.id)
    return CourseOfferingOut(
        id=offering.id, course_id=offering.course_id, course_code=course.code if course else "Unknown",
        course_title=course.title if course else "Unknown", credit_hours=course.credit_hours if course else 0,
        term_label=offering.term_label, assigned_teacher_id=offering.assigned_teacher_id,
        teacher_name=teacher_name, is_open_for_enrollment=offering.is_open_for_enrollment,
        has_syllabus=offering.syllabus_storage_key is not None, syllabus_original_name=offering.syllabus_original_name,
        enrolled_count=enrolled_count, attendance_locked_at=offering.attendance_locked_at,
        term_start_date=offering.term_start_date, term_end_date=offering.term_end_date,
        section_label=offering.section_label, capacity=offering.capacity,
        content_locked=offering.content_locked_at is not None,
        curriculum=(await _curriculum_labels(db, [offering.course_id])).get(offering.course_id, []),
    )


async def _to_offering_out_many(db, offerings: list) -> list[CourseOfferingOut]:
    """Same result as calling _to_offering_out on each row, but the courses, teachers and enrolment
    counts for the whole list are fetched in three queries in total instead of three per row."""
    courses = await load_by_ids(db, Course, [o.course_id for o in offerings])
    teachers = await load_by_ids(db, Employee, [o.assigned_teacher_id for o in offerings])
    counts = await enrolled_counts(db, [o.id for o in offerings])
    curriculum = await _curriculum_labels(db, [o.course_id for o in offerings])
    out = []
    for offering in offerings:
        course = courses.get(offering.course_id)
        teacher = teachers.get(offering.assigned_teacher_id)
        out.append(CourseOfferingOut(
            id=offering.id, course_id=offering.course_id, course_code=course.code if course else "Unknown",
            course_title=course.title if course else "Unknown", credit_hours=course.credit_hours if course else 0,
            term_label=offering.term_label, assigned_teacher_id=offering.assigned_teacher_id,
            teacher_name=f"{teacher.first_name} {teacher.last_name}" if teacher else None,
            is_open_for_enrollment=offering.is_open_for_enrollment,
            has_syllabus=offering.syllabus_storage_key is not None, syllabus_original_name=offering.syllabus_original_name,
            enrolled_count=counts.get(offering.id, 0), attendance_locked_at=offering.attendance_locked_at,
            term_start_date=offering.term_start_date, term_end_date=offering.term_end_date,
            section_label=offering.section_label, capacity=offering.capacity,
            content_locked=offering.content_locked_at is not None,
            curriculum=curriculum.get(offering.course_id, []),
        ))
    return out


async def _to_enrollment_out(db, enrollment) -> EnrollmentOut:
    # Explicit fetches, not .course_offering/.course relationships — see
    # the comment in _to_offering_out for why relationship access is
    # unsafe here (a freshly created/re-activated enrollment in this
    # same request never triggers even a selectin eager load).
    student = await db.get(Student, enrollment.student_id)
    offering = await db.get(CourseOffering, enrollment.course_offering_id)
    course = await db.get(Course, offering.course_id) if offering else None
    return EnrollmentOut(
        id=enrollment.id, student_id=enrollment.student_id,
        student_name=f"{student.first_name} {student.last_name}" if student else "Unknown",
        roll_number=student.roll_number if student else None,
        course_offering_id=enrollment.course_offering_id, course_code=course.code if course else "Unknown",
        course_title=course.title if course else "Unknown", credit_hours=course.credit_hours if course else 0,
        term_label=offering.term_label if offering else "Unknown", status=enrollment.status, outcome=enrollment.outcome,
        is_retake=enrollment.is_retake, enrolled_at=enrollment.enrolled_at,
    )


# ---------------------------------------------------------------------------
# Courses (curriculum design — ADMIN only to manage, broad to view)
# ---------------------------------------------------------------------------
@router.get("/courses", response_model=list[CourseOut], summary="List courses")
async def list_courses(
    active_only: bool = False,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_VIEW)),
    db=Depends(get_db),
) -> list[CourseOut]:
    courses = await CourseRepository(db).list_all(active_only=active_only)
    return [CourseOut.model_validate(c) for c in courses]


@router.post("/courses", response_model=CourseOut, status_code=201, summary="Create a course")
async def create_course(
    payload: CourseCreateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_MANAGE)),
    db=Depends(get_db),
) -> CourseOut:
    course = await CourseService(db).create(
        code=payload.code, title=payload.title, credit_hours=payload.credit_hours,
        description=payload.description, actor_id=current_user.id,
    )
    return CourseOut.model_validate(course)


@router.get("/courses/{course_id}", response_model=CourseOut, summary="Get a course")
async def get_course(
    course_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_VIEW)),
    db=Depends(get_db),
) -> CourseOut:
    course = await CourseService(db).get(course_id)
    return CourseOut.model_validate(course)


@router.patch("/courses/{course_id}", response_model=CourseOut, summary="Edit a course's title/credit hours/description/active status")
async def update_course(
    course_id: uuid.UUID,
    payload: CourseUpdateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_MANAGE)),
    db=Depends(get_db),
) -> CourseOut:
    kwargs = {"actor_id": current_user.id}
    if payload.title is not None:
        kwargs["title"] = payload.title
    if payload.credit_hours is not None:
        kwargs["credit_hours"] = payload.credit_hours
    if "description" in payload.model_fields_set:
        kwargs["description"] = payload.description
    if payload.is_active is not None:
        kwargs["is_active"] = payload.is_active
    course = await CourseService(db).update(course_id, **kwargs)
    return CourseOut.model_validate(course)


@router.post(
    "/courses/{course_id}/programs", response_model=CourseProgramOut, status_code=201,
    summary="Link a course into a program's curriculum at a given semester",
)
async def link_course_to_program(
    course_id: uuid.UUID,
    payload: CourseProgramLinkRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_MANAGE)),
    db=Depends(get_db),
) -> CourseProgramOut:
    link = await CourseService(db).link_to_program(
        course_id, program_id=payload.program_id, semester_number=payload.semester_number, actor_id=current_user.id
    )
    # Explicit fetch, not link.course — see _to_offering_out's comment;
    # `link` was just constructed and flushed in this same request, so
    # even the model's lazy="selectin" relationship never gets a chance
    # to fire (no fresh query ever happens for an object already in the
    # identity map). This is what actually crashed in production.
    course = await db.get(Course, course_id)
    program = await db.get(Program, payload.program_id)
    return CourseProgramOut(
        course_id=course_id, program_id=payload.program_id, semester_number=link.semester_number,
        course_code=course.code if course else "Unknown", course_title=course.title if course else "Unknown",
        credit_hours=course.credit_hours if course else 0,
        program_name=program.name if program else "Unknown",
    )


@router.get(
    "/programs/{program_id}/curriculum", response_model=list[CourseProgramOut],
    summary="A program's full curriculum (which courses at which semester)",
)
async def get_program_curriculum(
    program_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_VIEW)),
    db=Depends(get_db),
) -> list[CourseProgramOut]:
    program = await db.get(Program, program_id)
    if program is None:
        raise ResourceNotFoundError("Program not found.")
    links = await CourseProgramRepository(db).list_for_program(program_id)
    result = []
    for link in links:
        # Explicit fetch, not link.course — consistent with the fix
        # above, even though this path (loaded via select()) is less
        # likely to hit the same crash; not worth the risk of relying
        # on relationship access anywhere in this module now.
        course = await db.get(Course, link.course_id)
        result.append(
            CourseProgramOut(
                course_id=link.course_id, program_id=program_id, semester_number=link.semester_number,
                course_code=course.code if course else "Unknown", course_title=course.title if course else "Unknown",
                credit_hours=course.credit_hours if course else 0,
                program_name=program.name,
            )
        )
    return result


@router.get(
    "/courses/{course_id}/programs", response_model=list[CourseProgramOut],
    summary="Which program(s) and semester(s) this course is taught in — 'in which class it will be taught'",
)
async def get_course_program_links(
    course_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_VIEW)),
    db=Depends(get_db),
) -> list[CourseProgramOut]:
    course = await CourseService(db).get(course_id)
    links = await CourseProgramRepository(db).list_for_course(course_id)
    result = []
    for link in links:
        program = await db.get(Program, link.program_id)
        result.append(
            CourseProgramOut(
                course_id=course_id, program_id=link.program_id, semester_number=link.semester_number,
                course_code=course.code, course_title=course.title, credit_hours=course.credit_hours,
                program_name=program.name if program else "Unknown",
            )
        )
    return result


@router.delete(
    "/courses/{course_id}/programs/{program_id}", status_code=204,
    summary="Remove a course from a program's curriculum",
)
async def unlink_course_from_program(
    course_id: uuid.UUID,
    program_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_MANAGE)),
    db=Depends(get_db),
) -> None:
    await CourseService(db).unlink_from_program(course_id, program_id, actor_id=current_user.id)


# ---------------------------------------------------------------------------
# Course offerings (term instances + teacher assignment)
# ---------------------------------------------------------------------------
@router.post("/offerings", response_model=CourseOfferingOut, status_code=201, summary="Create a course offering for a term")
async def create_offering(
    payload: CourseOfferingCreateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_OFFERING_MANAGE)),
    db=Depends(get_db),
) -> CourseOfferingOut:
    term = await AcademicTermService(db).require_usable(payload.term_label)
    if term is not None:   # dates default to the term's own dates
        payload.term_start_date = payload.term_start_date or term.start_date
        payload.term_end_date = payload.term_end_date or term.end_date
    offering = await CourseOfferingService(db).create(
        course_id=payload.course_id, term_label=payload.term_label,
        assigned_teacher_id=payload.assigned_teacher_id,
        term_start_date=payload.term_start_date, term_end_date=payload.term_end_date,
        section_label=payload.section_label, capacity=payload.capacity,
        actor_id=current_user.id,
    )
    return await _to_offering_out(db, offering)


@router.get("/offerings", response_model=list[CourseOfferingOut], summary="List course offerings for a term")
async def list_offerings(
    term_label: str,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_VIEW)),
    db=Depends(get_db),
) -> list[CourseOfferingOut]:
    repo = CourseOfferingRepository(db)
    offerings = await repo.list_by_term(await repo.canonical_term_label(term_label))
    return await _to_offering_out_many(db, offerings)


@router.get(
    "/offerings/browse-for-me", response_model=list[OfferingBrowseOut],
    summary="Open courses in any term, each marked with whether I may enroll right now (and why not) -- uses the real enrollment rules",
)
async def browse_offerings_for_me(
    term_label: str,
    current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_SELF)),
    db=Depends(get_db),
) -> list[OfferingBrowseOut]:
    student_id = await _resolve_own_student_id(db, current_user)
    repo = CourseOfferingRepository(db)
    term = await repo.canonical_term_label(term_label)
    offerings = [o for o in await repo.list_by_term(term) if o.is_open_for_enrollment][:150]
    offering_ids = [o.id for o in offerings]
    outs = await _to_offering_out_many(db, offerings)          # plain values first: the checks below use savepoints
    service = EnrollmentService(db)
    results = []
    for out, offering_id in zip(outs, offering_ids):
        verdict = await service.explain_self_enrollment(student_id=student_id, offering_id=offering_id, actor_id=current_user.id)
        results.append(OfferingBrowseOut(**out.model_dump(), **verdict))
    # the ones the student CAN take first
    results.sort(key=lambda r: (not r.can_enroll, r.course_code, r.section_label))
    return results


@router.get(
    "/offerings/eligible-for-me", response_model=list[CourseOfferingOut],
    summary="Open offerings matching MY OWN program and current semester — what a student sees to enroll in, by default",
)
async def list_eligible_offerings_for_me(
    current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_SELF)),
    db=Depends(get_db),
) -> list[CourseOfferingOut]:
    student = await StudentRepository(db).get_by_user_id(current_user.id)
    if student is None:
        raise AuthorizationError("This account is not linked to a student record.")
    if student.program_id is None or student.current_semester is None:
        return []

    links = await CourseProgramRepository(db).list_for_program(student.program_id)
    course_ids = {link.course_id for link in links if link.semester_number == student.current_semester}
    if not course_ids:
        return []

    offering_repo = CourseOfferingRepository(db)
    out = []
    for course_id in course_ids:
        for offering in await offering_repo.list_by_course(course_id):
            if not offering.is_open_for_enrollment:
                continue
            # A student who belongs to a section only sees their own section's offerings.
            if student.section_label and offering.section_label.strip().lower() != student.section_label.strip().lower():
                continue
            out.append(await _to_offering_out(db, offering))
    return out


@router.get("/offerings/me", response_model=list[CourseOfferingOut], summary="My assigned course offerings (teacher)")
async def my_offerings(
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> list[CourseOfferingOut]:
    teacher_id = await _resolve_own_teacher_id(db, current_user)
    if teacher_id is None:
        raise AuthorizationError("This account is not linked to an employee record.")
    offerings = await CourseOfferingRepository(db).list_by_teacher(teacher_id)
    return await _to_offering_out_many(db, offerings)


@router.get(
    "/offerings/me/overview", response_model=list[TeacherCourseOverviewOut],
    summary="The courses I teach at a glance: enrolled, attendance recorded, syllabus progress, what needs attention (teacher)",
)
async def my_teaching_overview(
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> list[TeacherCourseOverviewOut]:
    from sqlalchemy import and_, case, func, select

    from app.academics.models import ClassSession
    from app.attendance.models import AttendanceRecord, AttendanceStatus

    teacher_id = await _resolve_own_teacher_id(db, current_user)
    if teacher_id is None:
        raise AuthorizationError("This account is not linked to an employee record.")
    today = institute_today()
    # current courses, plus ones that finished in the last 45 days (their attendance may still need tidying)
    offerings = [
        o for o in await CourseOfferingRepository(db).list_by_teacher(teacher_id)
        if o.term_end_date is None or o.term_end_date >= today - timedelta(days=45)
    ]
    if not offerings:
        return []
    ids = [o.id for o in offerings]
    courses = await load_by_ids(db, Course, [o.course_id for o in offerings])
    enrolled = await enrolled_counts(db, ids)
    live = ClassSession.status != ClassSessionStatus.CANCELLED
    sessions = {
        row[0]: row[1:] for row in (await db.execute(
            select(
                ClassSession.course_offering_id,
                func.count(),
                func.sum(case((ClassSession.attendance_taken_at.is_not(None), 1), else_=0)),
                func.sum(case((ClassSession.topic_completed.is_(True), 1), else_=0)),
                func.sum(case((and_(ClassSession.status == ClassSessionStatus.SCHEDULED, ClassSession.session_date < today,
                                    ClassSession.attendance_taken_at.is_(None)), 1), else_=0)),
                func.min(case((ClassSession.session_date >= today, ClassSession.session_date))),
            ).where(ClassSession.course_offering_id.in_(ids), live).group_by(ClassSession.course_offering_id)
        )).all()
    }
    attendance = {
        row[0]: row[1:] for row in (await db.execute(
            select(
                ClassSession.course_offering_id, func.count(AttendanceRecord.id),
                func.sum(case((AttendanceRecord.status == AttendanceStatus.PRESENT, 1), else_=0)),
            ).join(AttendanceRecord, AttendanceRecord.class_session_id == ClassSession.id)
            .where(ClassSession.course_offering_id.in_(ids), live).group_by(ClassSession.course_offering_id)
        )).all()
    }
    out = []
    for o in offerings:
        course = courses.get(o.course_id)
        total, held, covered, unmarked, next_date = sessions.get(o.id, (0, 0, 0, 0, None))
        records, present = attendance.get(o.id, (0, 0))
        out.append(TeacherCourseOverviewOut(
            offering_id=o.id, course_code=course.code if course else "Unknown", course_title=course.title if course else "Unknown",
            credit_hours=course.credit_hours if course else 0, section_label=o.section_label, term_label=o.term_label,
            enrolled_count=enrolled.get(o.id, 0), is_open_for_enrollment=o.is_open_for_enrollment,
            content_locked=o.content_locked_at is not None, classes_total=int(total), classes_held=int(held or 0),
            topics_covered=int(covered or 0), average_attendance=round(int(present or 0) / records * 100, 1) if records else None,
            unmarked_past_classes=int(unmarked or 0), next_class_date=next_date,
        ))
    return sorted(out, key=lambda c: (c.term_label, c.course_code, c.section_label))


@router.get(
    "/offerings/me/schedule", response_model=list[MyScheduleEntryOut],
    summary="My upcoming class sessions across every offering I teach, in one list (cancelled sessions excluded)",
)
async def my_schedule(
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> list[MyScheduleEntryOut]:
    teacher_id = await _resolve_own_teacher_id(db, current_user)
    if teacher_id is None:
        raise AuthorizationError("This account is not linked to an employee record.")
    offerings = await CourseOfferingRepository(db).list_by_teacher(teacher_id)
    session_service = ClassSessionService(db)

    entries = []
    for offering in offerings:
        course = await CourseRepository(db).get_by_id(offering.course_id)
        sessions = await session_service.list_for_offering(offering.id)
        for class_session in sessions:
            if class_session.status == ClassSessionStatus.CANCELLED:
                continue
            entries.append(
                MyScheduleEntryOut(
                    id=class_session.id, course_offering_id=offering.id,
                    course_code=course.code if course else "Unknown", course_title=course.title if course else "Unknown",
                    term_label=offering.term_label, session_date=class_session.session_date,
                    start_time=class_session.start_time,
                    end_time=class_session.end_time, room=class_session.room, title=class_session.title,
                )
            )
    entries.sort(key=lambda e: (e.session_date, e.start_time))
    return entries


@router.get(
    "/enrollments/me/overview", response_model=list[CourseOverviewOut],
    summary="My current courses at a glance: teacher, attendance and how much has been taught -- in one call",
)
async def my_courses_overview(
    current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_SELF)),
    db=Depends(get_db),
) -> list[CourseOverviewOut]:
    from sqlalchemy import case, func, select

    from app.attendance.models import AttendanceRecord, AttendanceStatus
    from app.academics.models import ClassSession

    student_id = await _resolve_own_student_id(db, current_user)
    enrollments = await EnrollmentRepository(db).list_current_for_student(student_id)
    offering_ids = [e.course_offering_id for e in enrollments]
    if not offering_ids:
        return []
    offerings = await load_by_ids(db, CourseOffering, offering_ids)
    courses = await load_by_ids(db, Course, [o.course_id for o in offerings.values()])
    teachers = await load_by_ids(db, Employee, [o.assigned_teacher_id for o in offerings.values()])

    # attendance: how many classes were held for me, and how many I attended -- one grouped query
    att = {
        oid: (int(held), int(present or 0))
        for oid, held, present in (await db.execute(
            select(
                ClassSession.course_offering_id, func.count(AttendanceRecord.id),
                func.sum(case((AttendanceRecord.status == AttendanceStatus.PRESENT, 1), else_=0)),
            )
            .join(AttendanceRecord, AttendanceRecord.class_session_id == ClassSession.id)
            .where(AttendanceRecord.student_id == student_id, ClassSession.course_offering_id.in_(offering_ids),
                   ClassSession.status != ClassSessionStatus.CANCELLED)
            .group_by(ClassSession.course_offering_id)
        )).all()
    }
    # syllabus progress: classes on the timetable vs classes the teacher marked "covered"
    topics = {
        oid: (int(total), int(covered or 0))
        for oid, total, covered in (await db.execute(
            select(
                ClassSession.course_offering_id, func.count(),
                func.sum(case((ClassSession.topic_completed.is_(True), 1), else_=0)),
            )
            .where(ClassSession.course_offering_id.in_(offering_ids), ClassSession.status != ClassSessionStatus.CANCELLED)
            .group_by(ClassSession.course_offering_id)
        )).all()
    }
    out = []
    for oid in offering_ids:
        o = offerings.get(oid)
        if o is None:
            continue
        course, teacher = courses.get(o.course_id), teachers.get(o.assigned_teacher_id)
        held, present = att.get(oid, (0, 0))
        total, covered = topics.get(oid, (0, 0))
        out.append(CourseOverviewOut(
            offering_id=oid, course_code=course.code if course else "Unknown", course_title=course.title if course else "Unknown",
            section_label=o.section_label, teacher_name=f"{teacher.first_name} {teacher.last_name}" if teacher else None,
            classes_held=held, classes_attended=present, attendance_percentage=round(present / held * 100, 1) if held else None,
            topics_total=total, topics_covered=covered,
        ))
    return sorted(out, key=lambda c: c.course_code)


@router.get(
    "/enrollments/me/schedule", response_model=list[MyScheduleEntryOut],
    summary="My upcoming class sessions across every course I'm currently enrolled in, in one list (cancelled sessions excluded)",
)
async def my_enrollments_schedule(
    current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_SELF)),
    db=Depends(get_db),
) -> list[MyScheduleEntryOut]:
    student_id = await _resolve_own_student_id(db, current_user)
    enrollments = await EnrollmentRepository(db).list_current_for_student(student_id)
    session_service = ClassSessionService(db)

    entries = []
    for enrollment in enrollments:
        offering = await CourseOfferingRepository(db).get_by_id(enrollment.course_offering_id)
        if offering is None:
            continue
        course = await CourseRepository(db).get_by_id(offering.course_id)
        teacher = await db.get(Employee, offering.assigned_teacher_id) if offering.assigned_teacher_id else None
        teacher_name = f"{teacher.first_name} {teacher.last_name}" if teacher else None
        sessions = await session_service.list_for_offering(offering.id)
        for class_session in sessions:
            if class_session.status == ClassSessionStatus.CANCELLED:
                continue
            entries.append(
                MyScheduleEntryOut(
                    id=class_session.id, course_offering_id=offering.id,
                    course_code=course.code if course else "Unknown", course_title=course.title if course else "Unknown",
                    term_label=offering.term_label, session_date=class_session.session_date,
                    start_time=class_session.start_time,
                    end_time=class_session.end_time, room=class_session.room, title=class_session.title,
                    teacher_name=teacher_name, section_label=offering.section_label,
                )
            )
    entries.sort(key=lambda e: (e.session_date, e.start_time))
    return entries


@router.get("/offerings/{offering_id}", response_model=CourseOfferingOut, summary="Get a course offering")
async def get_offering(
    offering_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_VIEW)),
    db=Depends(get_db),
) -> CourseOfferingOut:
    offering = await CourseOfferingService(db).get(offering_id)
    return await _to_offering_out(db, offering)


@router.patch(
    "/offerings/{offering_id}", response_model=CourseOfferingOut,
    summary="Assign/reassign a teacher and/or toggle enrollment open",
)
async def update_offering(
    offering_id: uuid.UUID,
    payload: CourseOfferingUpdateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_OFFERING_MANAGE)),
    db=Depends(get_db),
) -> CourseOfferingOut:
    kwargs = {"actor_id": current_user.id}
    if "assigned_teacher_id" in payload.model_fields_set:
        kwargs["assigned_teacher_id"] = payload.assigned_teacher_id
    if payload.is_open_for_enrollment is not None:
        kwargs["is_open_for_enrollment"] = payload.is_open_for_enrollment
    if "term_start_date" in payload.model_fields_set:
        kwargs["term_start_date"] = payload.term_start_date
    if "term_end_date" in payload.model_fields_set:
        kwargs["term_end_date"] = payload.term_end_date
    if "capacity" in payload.model_fields_set:
        kwargs["capacity"] = payload.capacity
    if payload.section_label is not None:
        kwargs["section_label"] = payload.section_label
    offering = await CourseOfferingService(db).update(offering_id, **kwargs)
    return await _to_offering_out(db, offering)


@router.post(
    "/offerings/generate", response_model=GenerateOfferingsResponse, status_code=201,
    summary="Open a whole semester: create one offering per curriculum course of a program/semester",
)
async def generate_offerings(
    payload: GenerateOfferingsRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_OFFERING_MANAGE)),
    db=Depends(get_db),
) -> GenerateOfferingsResponse:
    term = await AcademicTermService(db).require_usable(payload.term_label)
    if term is not None:
        payload.term_start_date = payload.term_start_date or term.start_date
        payload.term_end_date = payload.term_end_date or term.end_date
    result = await CourseOfferingService(db).generate_for_program_semester(
        term_label=payload.term_label, program_id=payload.program_id, semester_number=payload.semester_number,
        section_label=payload.section_label, term_start_date=payload.term_start_date,
        term_end_date=payload.term_end_date, capacity=payload.capacity,
        open_for_enrollment=payload.open_for_enrollment, actor_id=current_user.id,
    )
    return GenerateOfferingsResponse(**result)


@router.get(
    "/allocation-overview",
    summary="Is this term ready? Sections with missing teacher/timetable/dates, teacher loads, uncovered courses",
)
async def allocation_overview(
    term_label: str,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_OFFERING_MANAGE)),
    db=Depends(get_db),
) -> dict:
    return await AllocationService(db).overview(term_label)


@router.post(
    "/offerings/{offering_id}/enroll-student", response_model=EnrollmentOut, status_code=201,
    summary="Registrar: enroll one student in this offering (works even while self-enrollment is closed)",
)
async def admin_enroll_student(
    offering_id: uuid.UUID,
    payload: AdminEnrollRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_MANAGE)),
    db=Depends(get_db),
) -> EnrollmentOut:
    enrollment = await EnrollmentService(db).enroll(
        student_id=payload.student_id, course_offering_id=offering_id, actor_id=current_user.id, by_admin=True
    )
    return await _to_enrollment_out(db, enrollment)


@router.post(
    "/offerings/{offering_id}/enroll-batch", response_model=BatchEnrollResponse,
    summary="Registrar: enroll many students at once (explicit list, or program / semester / section)",
)
async def admin_enroll_batch(
    offering_id: uuid.UUID,
    payload: BatchEnrollRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_MANAGE)),
    db=Depends(get_db),
) -> BatchEnrollResponse:
    result = await EnrollmentService(db).enroll_many(
        course_offering_id=offering_id, actor_id=current_user.id, student_ids=payload.student_ids,
        program_id=payload.program_id, semester_number=payload.semester_number, section_label=payload.section_label,
    )
    return BatchEnrollResponse(**result)


@router.post(
    "/offerings/{offering_id}/lecture-plan",
    summary="Save the topic of many classes at once (a course plan) -- the offering's teacher, or admin",
)
async def save_lecture_plan(
    offering_id: uuid.UUID,
    payload: LecturePlanRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> dict:
    offering = await CourseOfferingService(db).get(offering_id)
    await _assert_can_edit_content(db, offering, current_user)
    updated, skipped_locked = await ClassSessionService(db).set_titles(
        offering_id, [(i.class_session_id, i.title) for i in payload.items], actor_id=current_user.id,
        skip_locked=Permissions.LECTURE_MANAGE not in current_user.permissions,
    )
    return {"updated": updated, "skipped_locked": skipped_locked}


@router.post(
    "/offerings/{offering_id}/content-lock", response_model=ContentLockResult,
    summary="Admin: lock or unlock a course's content so the teacher cannot change or delete it (whole course, or past classes only)",
)
async def set_offering_content_lock(
    offering_id: uuid.UUID,
    payload: ContentLockRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.LECTURE_MANAGE)),
    db=Depends(get_db),
) -> ContentLockResult:
    offering, changed = await CourseOfferingService(db).set_content_lock(
        offering_id, locked=payload.locked, scope=payload.scope, actor_id=current_user.id
    )
    return ContentLockResult(content_locked=offering.content_locked_at is not None, classes_changed=changed)


@router.post(
    "/class-sessions/{class_session_id}/content-lock", response_model=ClassSessionOut,
    summary="Admin: lock or unlock ONE class's content (topic, files, links)",
)
async def set_class_content_lock(
    class_session_id: uuid.UUID,
    payload: ClassContentLockRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.LECTURE_MANAGE)),
    db=Depends(get_db),
) -> ClassSessionOut:
    service = ClassSessionService(db)
    class_session = await service.set_content_lock(class_session_id, locked=payload.locked, actor_id=current_user.id)
    return _to_class_session_out(class_session, await service.list_resources(class_session_id))


@router.post(
    "/timetable/auto-schedule-term", response_model=BulkTimetableResult,
    summary="Timetable a whole term in one action: auto-schedule every offering that has no slots yet -- admin only",
)
async def auto_schedule_term(
    payload: AutoScheduleTermRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_OFFERING_MANAGE)),
    db=Depends(get_db),
) -> BulkTimetableResult:
    result = await TimetableService(db).auto_schedule_term(
        term_label=payload.term_label, rooms=payload.rooms, sessions_per_week=payload.sessions_per_week,
        actor_id=current_user.id,
    )
    return BulkTimetableResult(**result)


@router.post(
    "/timetable/copy-from-term", response_model=BulkTimetableResult,
    summary="Repeat a previous term's timetable (same course + section) into a new term -- admin only",
)
async def copy_timetable_from_term(
    payload: CopyTimetableRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_OFFERING_MANAGE)),
    db=Depends(get_db),
) -> BulkTimetableResult:
    result = await TimetableService(db).copy_from_term(
        source_term=payload.source_term, target_term=payload.target_term, actor_id=current_user.id
    )
    return BulkTimetableResult(**result)


@router.get(
    "/offerings/{offering_id}/roster", response_model=list[EnrollmentOut],
    summary="Everyone enrolled in this offering (admin, or its assigned teacher)",
)
async def get_offering_roster(
    offering_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> list[EnrollmentOut]:
    offering = await CourseOfferingService(db).get(offering_id)
    if Permissions.ENROLLMENT_MANAGE not in current_user.permissions:
        own_teacher_id = await _resolve_own_teacher_id(db, current_user)
        if own_teacher_id is None or offering.assigned_teacher_id != own_teacher_id:
            raise AuthorizationError("You are not the assigned teacher for this offering.")
    enrollments = await EnrollmentService(db).list_for_offering(offering_id)
    return [await _to_enrollment_out(db, e) for e in enrollments]


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------
@router.post("/enrollments/me", response_model=EnrollmentOut, status_code=201, summary="Enroll myself in a course offering")
async def enroll_self(
    payload: EnrollRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_SELF)),
    db=Depends(get_db),
) -> EnrollmentOut:
    student_id = await _resolve_own_student_id(db, current_user)
    from app.attendance.holds import ensure_no_attendance_hold

    await ensure_no_attendance_hold(db, student_id, action="Course registration")
    enrollment = await EnrollmentService(db).enroll(
        student_id=student_id, course_offering_id=payload.course_offering_id, actor_id=current_user.id
    )
    return await _to_enrollment_out(db, enrollment)


@router.get("/enrollments/me", response_model=list[EnrollmentOut], summary="My own enrollments")
async def my_enrollments(
    current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_VIEW)),
    db=Depends(get_db),
) -> list[EnrollmentOut]:
    student_id = await _resolve_own_student_id(db, current_user)
    enrollments = await EnrollmentService(db).list_for_student(student_id)
    return [await _to_enrollment_out(db, e) for e in enrollments]


@router.post(
    "/enrollments/{enrollment_id}/admin-withdraw", response_model=EnrollmentOut,
    summary="Withdraw any student from a course (admin)",
)
async def withdraw_admin(
    enrollment_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_MANAGE)),
    db=Depends(get_db),
) -> EnrollmentOut:
    enrollment = await EnrollmentService(db).withdraw(enrollment_id, student_id=None, actor_id=current_user.id)
    return await _to_enrollment_out(db, enrollment)


@router.post(
    "/enrollments/{enrollment_id}/outcome", response_model=EnrollmentOut,
    summary="Manually override a final Pass/Fail outcome (admin) — GradingService now computes this automatically once marks are complete; this is for genuine manual correction",
)
async def set_enrollment_outcome(
    enrollment_id: uuid.UUID,
    payload: SetOutcomeRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_MANAGE)),
    db=Depends(get_db),
) -> EnrollmentOut:
    enrollment = await EnrollmentService(db).set_outcome(enrollment_id, outcome=payload.outcome, actor_id=current_user.id)
    return await _to_enrollment_out(db, enrollment)


# ---------------------------------------------------------------------------
# Result module — institute-wide catalog of assessment types (admin),
# per-subject assessments picked from it (teacher/admin), and marks
# ---------------------------------------------------------------------------
@router.get(
    "/assessment-types", response_model=list[AssessmentTypeOut],
    summary="The institute-wide result-type catalog, in one list",
)
async def list_assessment_types(
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> list[AssessmentTypeOut]:
    types = await AssessmentTypeService(db).list_all()
    return [AssessmentTypeOut.model_validate(t) for t in types]


@router.post(
    "/assessment-types", response_model=AssessmentTypeOut, status_code=201,
    summary="Add a type to the institute-wide result catalog (e.g. 'Quiz 1', 10 marks) — admin only",
)
async def create_assessment_type(
    payload: AssessmentTypeCreateRequest,
    current_user: CurrentUser = Depends(require_any_permission(Permissions.ENROLLMENT_MANAGE, Permissions.RESULT_APPROVE)),
    db=Depends(get_db),
) -> AssessmentTypeOut:
    assessment_type = await AssessmentTypeService(db).create(
        name=payload.name, category=payload.category, max_marks=payload.max_marks, actor_id=current_user.id,
    )
    return AssessmentTypeOut.model_validate(assessment_type)


@router.delete(
    "/assessment-types/{assessment_type_id}", status_code=204,
    summary="Remove a type from the catalog — only if no subject is using it — admin only",
)
async def delete_assessment_type(
    assessment_type_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_any_permission(Permissions.ENROLLMENT_MANAGE, Permissions.RESULT_APPROVE)),
    db=Depends(get_db),
) -> None:
    await AssessmentTypeService(db).delete(assessment_type_id, actor_id=current_user.id)


async def _to_offering_assessment_out(db, offering_assessment) -> OfferingAssessmentOut:
    assessment_type = await db.get(AssessmentType, offering_assessment.assessment_type_id)
    return OfferingAssessmentOut(
        id=offering_assessment.id, course_offering_id=offering_assessment.course_offering_id,
        assessment_type_id=offering_assessment.assessment_type_id, assessment_type_name=assessment_type.name,
        category=assessment_type.category, max_marks=float(assessment_type.max_marks),
    )


@router.post(
    "/offerings/{offering_id}/assessments", response_model=OfferingAssessmentOut, status_code=201,
    summary="Add a catalog type to this subject's result structure (e.g. add 'Quiz 1' to this class)",
)
async def add_offering_assessment(
    offering_id: uuid.UUID,
    payload: AddOfferingAssessmentRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.MARKS_ENTER)),
    db=Depends(get_db),
) -> OfferingAssessmentOut:
    offering = await CourseOfferingService(db).get(offering_id)
    if not await _can_manage_marks_for_offering(db, offering, current_user):
        raise AuthorizationError("You are not the assigned teacher for this offering.")
    offering_assessment = await GradingService(db).add_offering_assessment(
        offering_id, assessment_type_id=payload.assessment_type_id, actor_id=current_user.id,
    )
    return await _to_offering_assessment_out(db, offering_assessment)


@router.get(
    "/offerings/{offering_id}/assessments", response_model=list[OfferingAssessmentOut],
    summary="This subject's result structure — which catalog types apply here",
)
async def list_offering_assessments(
    offering_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> list[OfferingAssessmentOut]:
    offering = await CourseOfferingService(db).get(offering_id)
    if not await _can_view_offering_content(db, offering, current_user):
        raise AuthorizationError("You do not have access to this offering.")
    offering_assessments = await GradingService(db).list_offering_assessments(offering_id)
    return [await _to_offering_assessment_out(db, oa) for oa in offering_assessments]


@router.delete(
    "/offering-assessments/{offering_assessment_id}", status_code=204,
    summary="Remove a type from this subject's result structure — only before any student has a mark recorded",
)
async def remove_offering_assessment(
    offering_assessment_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.MARKS_ENTER)),
    db=Depends(get_db),
) -> None:
    offering_assessment = await db.get(OfferingAssessment, offering_assessment_id)
    if offering_assessment is None:
        raise ResourceNotFoundError("This subject's assessment entry was not found.")
    offering = await CourseOfferingService(db).get(offering_assessment.course_offering_id)
    if not await _can_manage_marks_for_offering(db, offering, current_user):
        raise AuthorizationError("You are not the assigned teacher for this offering.")
    await GradingService(db).remove_offering_assessment(offering_assessment_id, actor_id=current_user.id)


async def _to_student_mark_detail(db, mark) -> StudentMarkDetail:
    offering_assessment = await db.get(OfferingAssessment, mark.offering_assessment_id)
    assessment_type = await db.get(AssessmentType, offering_assessment.assessment_type_id)
    base = StudentMarkOut.model_validate(mark).model_dump()
    return StudentMarkDetail(
        **base, assessment_type_name=assessment_type.name, category=assessment_type.category,
        max_marks=float(assessment_type.max_marks),
    )


@router.get("/offerings/{offering_id}/marks.csv", summary="Download the marks sheet: roster x components, with marks already entered")
async def marks_sheet_download(offering_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.MARKS_ENTER)),
                               db=Depends(get_db)) -> Response:
    from app.academics.marks_sheet import MarksSheetService

    offering = await CourseOfferingService(db).get(offering_id)
    if not await _can_manage_marks_for_offering(db, offering, current_user):
        raise AuthorizationError("You are not the assigned teacher for this offering.")
    return Response(content=await MarksSheetService(db).template(offering_id), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="marks-{offering_id}.csv"'})


@router.post("/offerings/{offering_id}/marks/upload", summary="Upload the filled marks sheet (dry_run=true only checks). Each cell is checked like a hand-entered mark.")
async def marks_sheet_upload(offering_id: uuid.UUID, file: UploadFile, dry_run: bool = True,
                             current_user: CurrentUser = Depends(require_permission(Permissions.MARKS_ENTER)), db=Depends(get_db)) -> dict:
    from app.academics.marks_sheet import MarksSheetService

    offering = await CourseOfferingService(db).get(offering_id)
    if not await _can_manage_marks_for_offering(db, offering, current_user):
        raise AuthorizationError("You are not the assigned teacher for this offering.")
    raw = await file.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise BusinessRuleViolationError("The file is larger than 2 MB.")
    is_admin = Permissions.ENROLLMENT_MANAGE in current_user.permissions or Permissions.RESULT_APPROVE in current_user.permissions
    return await MarksSheetService(db).upload(offering_id, raw, actor_id=current_user.id, is_admin=is_admin, dry_run=dry_run)


@router.post(
    "/enrollments/{enrollment_id}/marks/{offering_assessment_id}", response_model=StudentMarkDetail, status_code=201,
    summary="Enter a student's mark for one subject assessment. Marks stay a DRAFT (invisible to students) until the sheet is submitted, approved and published; after that only the administration can correct one, with a reason",
)
async def enter_mark(
    enrollment_id: uuid.UUID,
    offering_assessment_id: uuid.UUID,
    payload: EnterMarkRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.MARKS_ENTER)),
    db=Depends(get_db),
) -> StudentMarkDetail:
    enrollment = await EnrollmentRepository(db).get_by_id(enrollment_id)
    if enrollment is None:
        raise ResourceNotFoundError("Enrollment not found.")
    offering = await CourseOfferingService(db).get(enrollment.course_offering_id)
    if not await _can_manage_marks_for_offering(db, offering, current_user):
        raise AuthorizationError("You are not the assigned teacher for this offering.")

    mark = await GradingService(db).enter_mark(
        enrollment_id, offering_assessment_id, obtained_marks=payload.obtained_marks, actor_id=current_user.id,
        is_admin=Permissions.ENROLLMENT_MANAGE in current_user.permissions or Permissions.RESULT_APPROVE in current_user.permissions, correction_reason=payload.correction_reason,
    )
    return await _to_student_mark_detail(db, mark)


@router.get(
    "/enrollments/{enrollment_id}/marks", response_model=list[StudentMarkDetail],
    summary="A student's marks for one enrollment — the student themself, the offering's teacher, or admin",
)
async def get_marks_for_enrollment(
    enrollment_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> list[StudentMarkDetail]:
    enrollment = await EnrollmentRepository(db).get_by_id(enrollment_id)
    if enrollment is None:
        raise ResourceNotFoundError("Enrollment not found.")
    offering = await CourseOfferingService(db).get(enrollment.course_offering_id)

    own_student = await StudentRepository(db).get_by_user_id(current_user.id)
    is_own = own_student is not None and own_student.id == enrollment.student_id
    if not is_own and not await _can_manage_marks_for_offering(db, offering, current_user):
        raise AuthorizationError("You do not have access to this enrollment's marks.")

    # A student sees their marks only once the course's results are PUBLISHED; the teacher and the administration always see them.
    if is_own and not await _can_manage_marks_for_offering(db, offering, current_user):
        from app.academics.result_sheet_service import ResultSheetService

        sheet = await ResultSheetService(db).get(enrollment.course_offering_id)
        if sheet is None or sheet.status != ResultSheetStatus.PUBLISHED:
            return []
    marks = await GradingService(db).get_marks_for_enrollment(enrollment_id)
    return [await _to_student_mark_detail(db, mark) for mark in marks]


# ---------------------------------------------------------------------------
# Syllabus — one combined PDF per offering
# ---------------------------------------------------------------------------
@router.post("/offerings/{offering_id}/syllabus", response_model=CourseOfferingOut, summary="Upload/replace an offering's syllabus PDF")
async def upload_syllabus(
    offering_id: uuid.UUID,
    file: UploadFile,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> CourseOfferingOut:
    offering = await CourseOfferingService(db).get(offering_id)
    await _assert_can_edit_content(db, offering, current_user)
    content = await file.read()
    offering = await CourseOfferingService(db).upload_syllabus(
        offering_id, content=content, original_filename=file.filename or "syllabus.pdf",
        content_type=file.content_type or "", actor_id=current_user.id,
    )
    return await _to_offering_out(db, offering)


@router.get("/offerings/{offering_id}/syllabus", summary="Download an offering's syllabus PDF")
async def download_syllabus(
    offering_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> Response:
    offering = await CourseOfferingService(db).get(offering_id)
    if not await _can_view_offering_content(db, offering, current_user):
        raise AuthorizationError("You do not have access to this offering's materials.")
    path = get_file_path(offering.syllabus_storage_key)
    if path is None:
        raise ResourceNotFoundError("No syllabus uploaded for this offering.")
    return Response(
        content=path.read_bytes(), media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{offering.syllabus_original_name or "syllabus.pdf"}"',
            **SAFE_SERVE_HEADERS,
        },
    )


@router.delete("/offerings/{offering_id}/syllabus", response_model=CourseOfferingOut, summary="Remove an offering's syllabus PDF")
async def remove_syllabus(
    offering_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> CourseOfferingOut:
    offering = await CourseOfferingService(db).get(offering_id)
    await _assert_can_edit_content(db, offering, current_user)
    offering = await CourseOfferingService(db).remove_syllabus(offering_id, actor_id=current_user.id)
    return await _to_offering_out(db, offering)


# ---------------------------------------------------------------------------
# Timetable — recurring weekly slots (admin-managed, generates sessions)
# ---------------------------------------------------------------------------
async def _timetable_scope(db, current_user: CurrentUser) -> dict:
    """Who is asking decides what the weekly timetable shows: the admin sees the whole
    institute, a teacher only their own courses, a student only what they are enrolled in."""
    if Permissions.ENROLLMENT_MANAGE in current_user.permissions:
        return {"scope": "admin"}
    student = await StudentRepository(db).get_by_user_id(current_user.id)
    if student is not None:
        return {"scope": "student", "student_id": student.id}
    employee = await EmployeeRepository(db).get_by_user_id(current_user.id)
    if employee is not None and Permissions.ATTENDANCE_MARK in current_user.permissions:
        return {"scope": "teacher", "employee_id": employee.id}
    raise AuthorizationError("There is no timetable for this account.")


def _timetable_filters(term_label, program_id, semester_number, section_label, teacher_id, room) -> dict:
    return {
        "term_label": term_label, "program_id": program_id, "semester_number": semester_number,
        "section_label": (section_label or "").strip() or None, "teacher_id": teacher_id,
        "room": (room or "").strip() or None,
    }


def _ics_response(events: list[dict], filename: str, name: str) -> Response:
    return Response(
        content=build_calendar(events, name=name), media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _event_from_entry(e) -> dict:
    return {"id": e.id, "course_code": e.course_code, "course_title": e.course_title, "session_date": e.session_date,
            "start_time": e.start_time, "end_time": e.end_time, "room": e.room, "teacher_name": e.teacher_name, "title": e.title}


@router.get(
    "/class-sessions/{class_session_id}/calendar.ics",
    summary="One class as a calendar file (opens in Google / Outlook / Apple / phone calendars)",
)
async def class_session_calendar(
    class_session_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db),
) -> Response:
    session_service = ClassSessionService(db)
    class_session = await session_service.get(class_session_id)
    offering = await CourseOfferingService(db).get(class_session.course_offering_id)
    if not await _can_view_offering_content(db, offering, current_user):
        raise AuthorizationError("You cannot see this class.")
    course = await CourseRepository(db).get_by_id(offering.course_id)
    teacher = await db.get(Employee, offering.assigned_teacher_id) if offering.assigned_teacher_id else None
    event = {"id": class_session.id, "course_code": course.code if course else "Class", "course_title": course.title if course else "",
             "session_date": class_session.session_date, "start_time": class_session.start_time, "end_time": class_session.end_time,
             "room": class_session.room, "teacher_name": f"{teacher.first_name} {teacher.last_name}" if teacher else None,
             "title": class_session.title}
    return _ics_response([event], f"{event['course_code']}-{class_session.session_date}.ics", f"{event['course_code']} class")


@router.get(
    "/timetable/calendar.ics",
    summary="All my upcoming classes (next 4 months) as one calendar file -- students and teachers",
)
async def my_timetable_calendar(
    current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db),
) -> Response:
    scope = await _timetable_scope(db, current_user)
    if scope["scope"] == "student":
        entries = await my_enrollments_schedule(current_user=current_user, db=db)
    elif scope["scope"] == "teacher":
        entries = await my_schedule(current_user=current_user, db=db)
    else:
        raise AuthorizationError("Only students and teachers have a personal timetable.")
    today = institute_today()
    horizon = today + timedelta(days=120)
    events = [_event_from_entry(e) for e in entries if today <= e.session_date <= horizon]
    return _ics_response(events, "my-timetable.ics", "My VIST timetable")


@router.get(
    "/timetable/filters", response_model=TimetableFilterOptions,
    summary="The choices for the weekly timetable filters, limited to what this person may see",
)
async def weekly_timetable_filters(
    current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db),
) -> TimetableFilterOptions:
    scope = await _timetable_scope(db, current_user)
    return TimetableFilterOptions(**await WeeklyTimetableService(db).filter_options(**scope))


@router.get(
    "/timetable/weekly", response_model=WeeklyTimetableOut,
    summary="Weekly timetable as a grid (times down, weekdays across). Admin: whole institute; teacher: own courses; student: own courses",
)
async def weekly_timetable(
    term_label: str | None = None, program_id: uuid.UUID | None = None, semester_number: int | None = None,
    section_label: str | None = None, teacher_id: uuid.UUID | None = None, room: str | None = None,
    current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db),
) -> WeeklyTimetableOut:
    scope = await _timetable_scope(db, current_user)
    data = await WeeklyTimetableService(db).build(
        **scope, **_timetable_filters(term_label, program_id, semester_number, section_label, teacher_id, room)
    )
    return WeeklyTimetableOut(**data)


@router.get(
    "/timetable/weekly.pdf",
    summary="The same weekly timetable as a printable PDF (landscape A4), for the same filters",
)
async def weekly_timetable_pdf(
    term_label: str | None = None, program_id: uuid.UUID | None = None, semester_number: int | None = None,
    section_label: str | None = None, teacher_id: uuid.UUID | None = None, room: str | None = None,
    current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db),
) -> Response:
    import re

    scope = await _timetable_scope(db, current_user)
    data = await WeeklyTimetableService(db).build(
        **scope, **_timetable_filters(term_label, program_id, semester_number, section_label, teacher_id, room)
    )
    try:
        pdf = render_pdf(data)
    except ImportError:
        return JSONResponse(status_code=503, content={"error": {
            "code": "PDF_UNAVAILABLE",
            "message": "PDF downloads are not set up on this server yet. Ask whoever manages the server to run: pip install reportlab",
        }})
    except Exception:
        logger.exception("weekly_timetable_pdf_failed")
        return JSONResponse(status_code=500, content={"error": {
            "code": "PDF_FAILED", "message": "The PDF could not be created. Please try again, or tell the administrator.",
        }})
    slug = re.sub(r"[^A-Za-z0-9]+", "-", data["title"]).strip("-").lower() or "timetable"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="timetable-{slug}.pdf"'},
    )


@router.get(
    "/timetable-slots", response_model=list[InstituteTimetableSlotOut],
    summary="Every recurring timetable slot in the institute, in one list — admin only",
)
async def list_institute_timetable(
    current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_MANAGE)),
    db=Depends(get_db),
) -> list[InstituteTimetableSlotOut]:
    slots = await TimetableService(db).slot_repo.list_all()
    offerings = await load_by_ids(db, CourseOffering, [sl.course_offering_id for sl in slots])
    courses = await load_by_ids(db, Course, [o.course_id for o in offerings.values()])
    teachers = await load_by_ids(db, Employee, [o.assigned_teacher_id for o in offerings.values()])
    out = []
    for slot in slots:
        offering = offerings.get(slot.course_offering_id)
        if offering is None:
            continue
        course = courses.get(offering.course_id)
        teacher = teachers.get(offering.assigned_teacher_id)
        out.append(
            InstituteTimetableSlotOut(
                id=slot.id, course_offering_id=slot.course_offering_id,
                course_code=course.code if course else "Unknown", course_title=course.title if course else "Unknown",
                term_label=offering.term_label, teacher_name=f"{teacher.first_name} {teacher.last_name}" if teacher else None,
                day_of_week=slot.day_of_week, start_time=slot.start_time, end_time=slot.end_time, room=slot.room,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Periods — institute-wide daily period structure, admin-defined once
# ---------------------------------------------------------------------------
@router.get("/periods", response_model=list[PeriodOut], summary="Every defined period, in order")
async def list_periods(
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> list[PeriodOut]:
    periods = await PeriodService(db).list_all()
    return [PeriodOut.model_validate(p) for p in periods]


@router.post("/periods", response_model=PeriodOut, status_code=201, summary="Define a new daily period — admin only")
async def create_period(
    payload: PeriodCreateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_MANAGE)),
    db=Depends(get_db),
) -> PeriodOut:
    period = await PeriodService(db).create(
        period_number=payload.period_number, start_time=payload.start_time, end_time=payload.end_time,
        actor_id=current_user.id,
    )
    return PeriodOut.model_validate(period)


@router.delete("/periods/{period_id}", status_code=204, summary="Remove a period — admin only")
async def delete_period(
    period_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_MANAGE)),
    db=Depends(get_db),
) -> None:
    await PeriodService(db).delete(period_id, actor_id=current_user.id)


@router.post(
    "/offerings/{offering_id}/timetable-slots", response_model=TimetableSlotCreatedOut, status_code=201,
    summary="Add a recurring weekly slot to an offering's timetable — runs the room/teacher/student-batch conflict check and generates one ClassSession per matching weekday in the offering's term",
)
async def create_timetable_slot(
    offering_id: uuid.UUID,
    payload: TimetableSlotCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> TimetableSlotCreatedOut:
    offering = await CourseOfferingService(db).get(offering_id)
    if not await _can_manage_offering(db, offering, current_user):
        raise AuthorizationError("You are not the assigned teacher for this offering.")
    slot, generated_count = await TimetableService(db).create_slot(
        course_offering_id=offering_id, period_id=payload.period_id, day_of_week=payload.day_of_week,
        room=payload.room, actor_id=current_user.id,
    )
    return TimetableSlotCreatedOut(slot=TimetableSlotOut.model_validate(slot), sessions_generated=generated_count)


@router.post(
    "/offerings/{offering_id}/timetable-slots/auto-assign", response_model=AutoAssignResultOut,
    summary="Auto-schedule N sessions/week for this offering — a simple greedy assigner, not a full optimizer; see the endpoint's own limits in the docs",
)
async def auto_assign_timetable(
    offering_id: uuid.UUID,
    payload: AutoAssignRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> AutoAssignResultOut:
    offering = await CourseOfferingService(db).get(offering_id)
    if not await _can_manage_offering(db, offering, current_user):
        raise AuthorizationError("You are not the assigned teacher for this offering.")
    slots, generated_count = await TimetableService(db).auto_assign(
        course_offering_id=offering_id, sessions_per_week=payload.sessions_per_week,
        room=payload.room, actor_id=current_user.id,
    )
    return AutoAssignResultOut(
        slots=[TimetableSlotOut.model_validate(s) for s in slots], sessions_generated=generated_count
    )


@router.get(
    "/offerings/{offering_id}/timetable-slots", response_model=list[TimetableSlotOut],
    summary="An offering's recurring weekly timetable slots",
)
async def list_timetable_slots(
    offering_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> list[TimetableSlotOut]:
    offering = await CourseOfferingService(db).get(offering_id)
    if not await _can_view_offering_content(db, offering, current_user):
        raise AuthorizationError("You do not have access to this offering's timetable.")
    slots = await TimetableService(db).list_slots(offering_id)
    return [TimetableSlotOut.model_validate(s) for s in slots]


@router.delete(
    "/timetable-slots/{slot_id}", status_code=200,
    summary="Remove a recurring slot — cancels its not-yet-occurred sessions, leaves past sessions and their history untouched",
)
async def delete_timetable_slot(
    slot_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> dict:
    service = TimetableService(db)
    slot = await service.slot_repo.get_by_id(slot_id)
    if slot is None:
        raise ResourceNotFoundError("Timetable slot not found.")
    offering = await CourseOfferingService(db).get(slot.course_offering_id)
    if not await _can_manage_offering(db, offering, current_user):
        raise AuthorizationError("You are not the assigned teacher for this offering.")
    cancelled_count = await service.delete_slot(slot_id, actor_id=current_user.id)
    return {"future_sessions_cancelled": cancelled_count}


# ---------------------------------------------------------------------------
# Class sessions — generated occurrences; attendance + per-session content
# ---------------------------------------------------------------------------
@router.get(
    "/offerings/{offering_id}/class-sessions", response_model=list[ClassSessionOut],
    summary="An offering's generated class sessions, with their resources",
)
async def list_class_sessions(
    offering_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> list[ClassSessionOut]:
    offering = await CourseOfferingService(db).get(offering_id)
    if not await _can_view_offering_content(db, offering, current_user):
        raise AuthorizationError("You do not have access to this offering's materials.")
    service = ClassSessionService(db)
    sessions = await service.list_for_offering(offering_id)
    out = []
    for class_session in sessions:
        resources = await service.list_resources(class_session.id)
        out.append(_to_class_session_out(class_session, resources))
    return out


@router.patch(
    "/class-sessions/{class_session_id}", response_model=ClassSessionOut,
    summary="Per-session override — reschedule (date/time/room) or edit the topic/description of ONE session only",
)
async def update_class_session(
    class_session_id: uuid.UUID,
    payload: ClassSessionUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> ClassSessionOut:
    service = ClassSessionService(db)
    class_session = await service.get(class_session_id)
    offering = await CourseOfferingService(db).get(class_session.course_offering_id)
    is_admin = await _can_manage_offering(db, offering, current_user)
    if not is_admin:
        await _assert_can_edit_content(db, offering, current_user, class_session)
        if any(v is not None for v in (payload.session_date, payload.start_time, payload.end_time, payload.room)):
            raise AuthorizationError("Only the admin can change a class's date, time or room. You can edit its topic and description.")

    kwargs = {"actor_id": current_user.id}
    if payload.session_date is not None:
        kwargs["session_date"] = payload.session_date
    if payload.start_time is not None:
        kwargs["start_time"] = payload.start_time
    if payload.end_time is not None:
        kwargs["end_time"] = payload.end_time
    if payload.room is not None:
        kwargs["room"] = payload.room
    if "title" in payload.model_fields_set:
        kwargs["title"] = payload.title
    if "description" in payload.model_fields_set:
        kwargs["description"] = payload.description
    class_session = await service.update(class_session_id, **kwargs)
    resources = await service.list_resources(class_session_id)
    return _to_class_session_out(class_session, resources)


@router.post(
    "/class-sessions/cancel-day",
    summary="Cancel every still-scheduled class on one date (holiday / strike) in one action -- admin only",
)
async def cancel_day(
    payload: CancelDayRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_OFFERING_MANAGE)),
    db=Depends(get_db),
) -> dict:
    return await ClassSessionService(db).cancel_day(
        payload.session_date, reason=payload.reason, term_label=payload.term_label, actor_id=current_user.id
    )


@router.post(
    "/class-sessions/{class_session_id}/cancel", response_model=ClassSessionOut,
    summary="Cancel one specific session (e.g. a holiday) without touching the recurring slot or any other session",
)
async def cancel_class_session(
    class_session_id: uuid.UUID,
    payload: ClassSessionCancelRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> ClassSessionOut:
    service = ClassSessionService(db)
    class_session = await service.get(class_session_id)
    offering = await CourseOfferingService(db).get(class_session.course_offering_id)
    if not await _can_manage_offering(db, offering, current_user):
        raise AuthorizationError("You are not the assigned teacher for this offering.")
    class_session = await service.cancel(class_session_id, reason=payload.reason, actor_id=current_user.id)
    resources = await service.list_resources(class_session_id)
    return _to_class_session_out(class_session, resources)


@router.post(
    "/class-sessions/{class_session_id}/topic-completed", response_model=ClassSessionOut,
    summary="Mark whether today's topic was actually covered — teacher of the offering, or admin",
)
async def set_topic_completed(
    class_session_id: uuid.UUID,
    payload: SetTopicCompletedRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> ClassSessionOut:
    service = ClassSessionService(db)
    class_session = await service.get(class_session_id)
    offering = await CourseOfferingService(db).get(class_session.course_offering_id)
    if not await _can_manage_marks_for_offering(db, offering, current_user):
        raise AuthorizationError("You are not the assigned teacher for this offering.")
    from app.attendance.service import AttendanceService

    class_session = await AttendanceService(db).set_topic_completed(
        class_session_id, completed=payload.completed, actor_id=current_user.id
    )
    resources = await service.list_resources(class_session_id)
    return _to_class_session_out(class_session, resources)


@router.get(
    "/offerings/{offering_id}/progress", response_model=CourseProgressOut,
    summary="How far along this offering is versus where it should be — teacher of the offering, or admin",
)
async def get_offering_progress(
    offering_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> CourseProgressOut:
    offering = await CourseOfferingService(db).get(offering_id)
    if not await _can_manage_marks_for_offering(db, offering, current_user):
        raise AuthorizationError("You do not have access to this offering's progress.")
    result = await CourseProgressService(db).compute_for_offering(offering_id)
    return CourseProgressOut(**result)


@router.get(
    "/teacher-progress", response_model=list[TeacherProgressEntry],
    summary="Every teacher's course progress across their assigned offerings — admin only",
)
async def teacher_progress(
    current_user: CurrentUser = Depends(require_any_permission(Permissions.ENROLLMENT_MANAGE, Permissions.ACADEMIC_OVERSIGHT)),
    db=Depends(get_db),
) -> list[TeacherProgressEntry]:
    from sqlalchemy import select

    offering_repo = CourseOfferingRepository(db)
    progress_service = CourseProgressService(db)

    teacher_ids_result = await db.execute(
        select(CourseOffering.assigned_teacher_id).where(CourseOffering.assigned_teacher_id.is_not(None)).distinct()
    )
    teacher_ids = list(teacher_ids_result.scalars())

    entries = []
    for teacher_id in teacher_ids:
        teacher = await db.get(Employee, teacher_id)
        if teacher is None:
            continue
        offerings = await offering_repo.list_by_teacher(teacher_id)
        for offering in offerings:
            course = await CourseRepository(db).get_by_id(offering.course_id)
            result = await progress_service.compute_for_offering(offering.id)
            entries.append(
                TeacherProgressEntry(
                    teacher_id=teacher_id, teacher_name=f"{teacher.first_name} {teacher.last_name}",
                    employee_code=teacher.employee_code,
                    course_code=course.code if course else "Unknown", course_title=course.title if course else "Unknown",
                    term_label=offering.term_label, **result,
                )
            )
    return entries


@router.post(
    "/class-sessions/{class_session_id}/resources", response_model=ClassSessionResourceOut, status_code=201,
    summary="Upload a file resource (notes/slides/other PDF) to a class session",
)
async def upload_class_session_resource(
    class_session_id: uuid.UUID,
    resource_type: LectureResourceType,
    file: UploadFile,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> ClassSessionResourceOut:
    service = ClassSessionService(db)
    class_session = await service.get(class_session_id)
    offering = await CourseOfferingService(db).get(class_session.course_offering_id)
    await _assert_can_edit_content(db, offering, current_user, class_session)
    content = await file.read()
    resource = await service.add_file_resource(
        class_session_id, resource_type=resource_type, content=content,
        original_filename=file.filename or "resource.pdf", content_type=file.content_type or "",
        actor_id=current_user.id,
    )
    return ClassSessionResourceOut.model_validate(resource)


@router.post(
    "/class-sessions/{class_session_id}/resources/link", response_model=ClassSessionResourceOut, status_code=201,
    summary="Add a link resource (e.g. a Slides URL) to a class session — same list as uploaded files",
)
async def add_class_session_link_resource(
    class_session_id: uuid.UUID,
    payload: LinkResourceCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> ClassSessionResourceOut:
    service = ClassSessionService(db)
    class_session = await service.get(class_session_id)
    offering = await CourseOfferingService(db).get(class_session.course_offering_id)
    await _assert_can_edit_content(db, offering, current_user, class_session)
    resource = await service.add_link_resource(
        class_session_id, resource_type=payload.resource_type, external_url=payload.external_url,
        title=payload.title, actor_id=current_user.id,
    )
    return ClassSessionResourceOut.model_validate(resource)


@router.get("/class-session-resources/{resource_id}", summary="Download a class session resource")
async def download_class_session_resource(
    resource_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> Response:
    service = ClassSessionService(db)
    resource = await service.get_resource(resource_id)
    class_session = await service.get(resource.class_session_id)
    offering = await CourseOfferingService(db).get(class_session.course_offering_id)
    if not await _can_view_offering_content(db, offering, current_user):
        raise AuthorizationError("You do not have access to this offering's materials.")
    if resource.storage_key is None:
        raise BusinessRuleViolationError("This is a link resource, not an uploaded file — open external_url directly.")
    path = get_file_path(resource.storage_key)
    if path is None:
        raise ResourceNotFoundError("File not found.")
    return Response(
        content=path.read_bytes(), media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{resource.original_name}"',
            **SAFE_SERVE_HEADERS,
        },
    )


@router.delete("/class-session-resources/{resource_id}", status_code=204, summary="Remove a class session resource")
async def delete_class_session_resource(
    resource_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> None:
    service = ClassSessionService(db)
    resource = await service.get_resource(resource_id)
    class_session = await service.get(resource.class_session_id)
    offering = await CourseOfferingService(db).get(class_session.course_offering_id)
    await _assert_can_edit_content(db, offering, current_user, class_session)
    await service.remove_resource(resource_id, actor_id=current_user.id)


# ---------------------------------------------------------------------------
# End of semester — "reset enrollment for next semester"
# ---------------------------------------------------------------------------
@router.get(
    "/end-semester/preview",
    summary="What End Semester would do for this term (read-only): offerings, enrollments to complete, ungraded",
)
async def end_semester_preview(
    term_label: str,
    current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_MANAGE)),
    db=Depends(get_db),
) -> dict:
    return await EnrollmentService(db).preview_end_semester(term_label)


@router.post(
    "/end-semester", response_model=EndSemesterResponse,
    summary="Close a term's offerings to enrollment and finalize its active enrollments to Completed",
)
async def end_semester(
    term_label: str,
    current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_MANAGE)),
    db=Depends(get_db),
) -> EndSemesterResponse:
    result = await EnrollmentService(db).end_semester(term_label, actor_id=current_user.id)
    return EndSemesterResponse(**result)


# ---------------------------------------------------------------------------
# Term list (for the admin UI's "load" dropdown) + teacher performance
# ---------------------------------------------------------------------------
@router.get("/terms", response_model=list[str], summary="Every term label in use: registered terms first (newest first), then any older label that only exists on offerings")
async def list_terms(
    current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_VIEW)),
    db=Depends(get_db),
) -> list[str]:
    registered = [t.label for t in await AcademicTermService(db).all()]
    return registered + [t for t in await CourseOfferingRepository(db).list_distinct_terms() if t not in registered]


class AcademicTermIn(BaseModel):
    label: str = Field(min_length=1, max_length=50)
    start_date: date
    end_date: date


class AcademicTermPatch(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    status: TermStatus | None = None


@router.get("/academic-terms", summary="The institute's academic terms with dates and status")
async def list_academic_terms(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    # Term names and dates are not sensitive, and several screens need them (fee structures, datesheets, admissions)
    # for people without course permissions, e.g. the accounts office.
    return [to_dict(t) for t in await AcademicTermService(db).all()]


@router.post("/academic-terms", status_code=201, summary="Add a term (administration)")
async def create_academic_term(payload: AcademicTermIn, current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_OFFERING_MANAGE)),
                               db=Depends(get_db)) -> dict:
    return to_dict(await AcademicTermService(db).create(label=payload.label, start_date=payload.start_date, end_date=payload.end_date,
                                                        actor_id=current_user.id))


@router.patch("/academic-terms/{term_id}", summary="Change a term's dates, or close/reopen it (administration). Labels cannot be renamed.")
async def update_academic_term(term_id: uuid.UUID, payload: AcademicTermPatch,
                               current_user: CurrentUser = Depends(require_permission(Permissions.COURSE_OFFERING_MANAGE)), db=Depends(get_db)) -> dict:
    return to_dict(await AcademicTermService(db).update(term_id, start_date=payload.start_date, end_date=payload.end_date,
                                                        status=payload.status, actor_id=current_user.id))


@router.get(
    "/teacher-performance", response_model=list[TeacherPerformanceEntry],
    summary="Per-teacher summary: courses taught, students taught, pass rate, complaints against them",
)
async def teacher_performance(
    current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_MANAGE)),
    db=Depends(get_db),
) -> list[TeacherPerformanceEntry]:
    from sqlalchemy import case, func, select

    from app.academics.models import Enrollment, EnrollmentOutcome
    from app.complaints.models import Complaint, ComplaintPartyType

    # The database does the counting (three grouped queries in total) instead of loading every
    # enrolment and complaint into memory one teacher at a time.
    offerings_per_teacher = {
        tid: n for tid, n in (await db.execute(
            select(CourseOffering.assigned_teacher_id, func.count())
            .where(CourseOffering.assigned_teacher_id.is_not(None)).group_by(CourseOffering.assigned_teacher_id)
        )).all()
    }
    outcome_rows = (await db.execute(
        select(
            CourseOffering.assigned_teacher_id,
            func.count(Enrollment.id),
            func.sum(case((Enrollment.outcome == EnrollmentOutcome.PASSED, 1), else_=0)),
            func.sum(case((Enrollment.outcome == EnrollmentOutcome.FAILED, 1), else_=0)),
        )
        .join(Enrollment, Enrollment.course_offering_id == CourseOffering.id)
        .where(CourseOffering.assigned_teacher_id.is_not(None))
        .group_by(CourseOffering.assigned_teacher_id)
    )).all()
    outcomes = {tid: (total, int(passed or 0), int(failed or 0)) for tid, total, passed, failed in outcome_rows}
    complaint_counts = {
        tid: n for tid, n in (await db.execute(
            select(Complaint.target_id, func.count())
            .where(Complaint.target_type == ComplaintPartyType.EMPLOYEE, Complaint.target_id.in_(list(offerings_per_teacher)))
            .group_by(Complaint.target_id)
        )).all()
    } if offerings_per_teacher else {}
    teachers = await load_by_ids(db, Employee, list(offerings_per_teacher))

    entries = []
    for teacher_id, offerings_count in offerings_per_teacher.items():
        teacher = teachers.get(teacher_id)
        if teacher is None:
            continue
        total_enrollments, passed_count, failed_count = outcomes.get(teacher_id, (0, 0, 0))
        graded_count = passed_count + failed_count
        entries.append(
            TeacherPerformanceEntry(
                teacher_id=teacher_id, teacher_name=f"{teacher.first_name} {teacher.last_name}",
                employee_code=teacher.employee_code, offerings_count=offerings_count,
                total_enrollments=total_enrollments, passed_count=passed_count, failed_count=failed_count,
                pass_rate=(passed_count / graded_count) if graded_count > 0 else None,
                complaints_against=complaint_counts.get(teacher_id, 0),
            )
        )

    entries.sort(key=lambda e: e.teacher_name)
    return entries