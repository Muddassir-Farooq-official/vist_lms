from __future__ import annotations

from app.core.csvsafe import safe_writer
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.academics.repository import ClassSessionRepository, CourseOfferingRepository, CourseRepository
from app.attendance.repository import AttendanceRepository
from app.auth.permissions import Roles
from app.attendance.schemas import (
    LockAttendanceRequest,
    AttendanceConfigOut,
    AttendanceRecordDetail,
    CorrectionDecision,
    CorrectionRequestCreate,
    CorrectionRequestOut,
    LockPreviewOut,
    MyAttendanceRecordOut,
    LockAttendanceResultOut,
    MarkSessionRequest,
    OfferingAttendancePercentagesOut,
    OverdueSessionOut,
    StudentAttendancePercentageOut,
)
from app.attendance.service import AttendanceService
from app.auth.permissions import Permissions
from app.core.clock import institute_now
from app.core.config import settings
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_any_permission, require_permission
from app.core.exceptions import AuthorizationError, ResourceNotFoundError
from app.employees.repository import EmployeeRepository
from app.students.repository import StudentRepository

router = APIRouter(prefix="/attendance", tags=["attendance"])


async def _resolve_own_teacher_id(db, current_user: CurrentUser) -> uuid.UUID | None:
    employee = await EmployeeRepository(db).get_by_user_id(current_user.id)
    return employee.id if employee else None


async def _resolve_own_student_id(db, current_user: CurrentUser) -> uuid.UUID | None:
    student = await StudentRepository(db).get_by_user_id(current_user.id)
    return student.id if student else None


async def _assert_can_mark_or_view_offering(db, offering_id: uuid.UUID, current_user: CurrentUser) -> None:
    """ADMIN can mark/view any offering. A TEACHER can only mark/view
    the ones they're actually assigned to -- matches the same
    ownership-check pattern used everywhere else offering content is
    gated (see app/academics/router.py::_can_view_offering_content)."""
    if Permissions.ATTENDANCE_LOCK in current_user.permissions:
        return  # ADMIN holds this; nobody else does
    offering = await CourseOfferingRepository(db).get_by_id(offering_id)
    if offering is None:
        raise ResourceNotFoundError("Course offering not found.")
    own_teacher_id = await _resolve_own_teacher_id(db, current_user)
    if own_teacher_id is None or offering.assigned_teacher_id != own_teacher_id:
        raise AuthorizationError("You are not the assigned teacher for this offering.")


async def _to_record_detail(db, record) -> AttendanceRecordDetail:
    student = await StudentRepository(db).get_by_id(record.student_id)
    return AttendanceRecordDetail(
        id=record.id, class_session_id=record.class_session_id, student_id=record.student_id,
        status=record.status, marked_at=record.marked_at,
        student_name=f"{student.first_name} {student.last_name}" if student else "Unknown",
        roll_number=student.roll_number if student else None,
    )


@router.post(
    "/sessions/{class_session_id}/mark", response_model=list[AttendanceRecordDetail],
    summary="Mark (or correct) attendance for one class session -- teacher of the offering, or admin",
)
async def mark_session(
    class_session_id: uuid.UUID,
    payload: MarkSessionRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.ATTENDANCE_MARK)),
    db=Depends(get_db),
) -> list[AttendanceRecordDetail]:
    class_session = await ClassSessionRepository(db).get_by_id(class_session_id)
    if class_session is None:
        raise ResourceNotFoundError("Class session not found.")
    await _assert_can_mark_or_view_offering(db, class_session.course_offering_id, current_user)

    records = await AttendanceService(db).mark_session(
        class_session_id, present_student_ids=payload.present_student_ids, actor_id=current_user.id,
        actor_is_admin=Permissions.ATTENDANCE_LOCK in current_user.permissions, late_note=payload.late_note,
    )
    return [await _to_record_detail(db, r) for r in records]


@router.get(
    "/needs-attention", response_model=list[OverdueSessionOut],
    summary="Sessions whose marking window closed with attendance never taken -- admin only",
)
async def needs_attention(
    response: Response,
    limit: int = 200,
    current_user: CurrentUser = Depends(require_any_permission(Permissions.ATTENDANCE_LOCK, Permissions.ATTENDANCE_CORRECTION_DECIDE)),
    db=Depends(get_db),
) -> list[OverdueSessionOut]:
    """Oldest first. Capped (default 200, header X-Total-Count says how many exist in all): nobody can
    act on hundreds of rows at once, and building them all made this the slowest page in the system.
    Related courses / teachers are loaded together, not one row at a time."""
    from app.academics.batch import load_by_ids
    from app.academics.models import Course, CourseOffering
    from app.employees.models import Employee

    from app.core.scope import hod_department, teacher_ids_in

    limit = max(1, min(limit, 1000))
    sessions = await AttendanceService(db).list_overdue_unmarked_sessions()
    dept = await hod_department(db, current_user)
    if dept:
        mine = await teacher_ids_in(db, dept)
        offs = await load_by_ids(db, CourseOffering, [cs.course_offering_id for cs in sessions])
        sessions = [cs for cs in sessions if offs.get(cs.course_offering_id) is not None and offs[cs.course_offering_id].assigned_teacher_id in mine]
    response.headers["X-Total-Count"] = str(len(sessions))
    sessions = sessions[:limit]
    offerings = await load_by_ids(db, CourseOffering, [cs.course_offering_id for cs in sessions])
    courses = await load_by_ids(db, Course, [o.course_id for o in offerings.values()])
    teachers = await load_by_ids(db, Employee, [o.assigned_teacher_id for o in offerings.values()])
    out = []
    for class_session in sessions:
        offering = offerings.get(class_session.course_offering_id)
        if offering is None:
            continue
        course = courses.get(offering.course_id)
        teacher = teachers.get(offering.assigned_teacher_id)
        out.append(
            OverdueSessionOut(
                id=class_session.id, course_offering_id=offering.id,
                course_code=course.code if course else "Unknown", course_title=course.title if course else "Unknown",
                teacher_name=f"{teacher.first_name} {teacher.last_name}" if teacher else None,
                session_date=class_session.session_date, start_time=class_session.start_time, room=class_session.room,
            )
        )
    return out


@router.get(
    "/sessions/{class_session_id}", response_model=list[AttendanceRecordDetail],
    summary="This class session's attendance sheet -- teacher of the offering, or admin",
)
async def get_session_attendance(
    class_session_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.ATTENDANCE_VIEW)),
    db=Depends(get_db),
) -> list[AttendanceRecordDetail]:
    class_session = await ClassSessionRepository(db).get_by_id(class_session_id)
    if class_session is None:
        raise ResourceNotFoundError("Class session not found.")
    await _assert_can_mark_or_view_offering(db, class_session.course_offering_id, current_user)

    records = await AttendanceRepository(db).list_for_session(class_session_id)
    return [await _to_record_detail(db, r) for r in records]


@router.get(
    "/me/offerings/{offering_id}/percentage", response_model=StudentAttendancePercentageOut,
    summary="My own attendance percentage for one course offering",
)
async def my_percentage(
    offering_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.ATTENDANCE_VIEW)),
    db=Depends(get_db),
) -> StudentAttendancePercentageOut:
    own_student_id = await _resolve_own_student_id(db, current_user)
    if own_student_id is None:
        raise AuthorizationError("This account is not linked to a student record.")
    result = await AttendanceService(db).compute_percentage(own_student_id, offering_id)
    return StudentAttendancePercentageOut(student_id=own_student_id, course_offering_id=offering_id, **result)


@router.get(
    "/offerings/{offering_id}/students/{student_id}/percentage", response_model=StudentAttendancePercentageOut,
    summary="Any student's attendance percentage for this offering -- teacher of the offering, or admin",
)
async def student_percentage(
    offering_id: uuid.UUID,
    student_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.ATTENDANCE_VIEW)),
    db=Depends(get_db),
) -> StudentAttendancePercentageOut:
    await _assert_can_mark_or_view_offering(db, offering_id, current_user)
    result = await AttendanceService(db).compute_percentage(student_id, offering_id)
    return StudentAttendancePercentageOut(student_id=student_id, course_offering_id=offering_id, **result)


@router.get(
    "/offerings/{offering_id}/percentages", response_model=OfferingAttendancePercentagesOut,
    summary="Every currently-enrolled student's attendance percentage for this offering, in one call -- teacher of the offering, or admin",
)
async def offering_percentages(
    offering_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.ATTENDANCE_VIEW)),
    db=Depends(get_db),
) -> OfferingAttendancePercentagesOut:
    await _assert_can_mark_or_view_offering(db, offering_id, current_user)
    raw = await AttendanceService(db).compute_percentages_for_offering(offering_id)
    percentages = {
        student_id: StudentAttendancePercentageOut(student_id=student_id, course_offering_id=offering_id, **result)
        for student_id, result in raw.items()
    }
    return OfferingAttendancePercentagesOut(course_offering_id=offering_id, percentages=percentages)


@router.post(
    "/offerings/{offering_id}/lock", response_model=LockAttendanceResultOut,
    summary="Finalize this offering's attendance for the term -- admin only, one-way",
)
async def lock_attendance(
    offering_id: uuid.UUID,
    payload: LockAttendanceRequest | None = None,
    current_user: CurrentUser = Depends(require_permission(Permissions.ATTENDANCE_LOCK)),
    db=Depends(get_db),
) -> LockAttendanceResultOut:
    result = await AttendanceService(db).lock_attendance(
        offering_id, actor_id=current_user.id, early_reason=payload.early_reason if payload else None,
        actor_is_admin=Roles.ADMIN in current_user.roles,
    )
    return LockAttendanceResultOut(course_offering_id=offering_id, **result)


# ---------------------------------------------------------------------------
# Config (so screens use the SERVER's clock and rules, never a hard-coded copy)
# ---------------------------------------------------------------------------
@router.get("/config", response_model=AttendanceConfigOut, summary="Marking window, thresholds and the server's current institute time")
async def attendance_config(
    current_user: CurrentUser = Depends(get_current_user),
) -> AttendanceConfigOut:
    return AttendanceConfigOut(
        marking_window_minutes=settings.attendance_marking_window_minutes,
        server_now=institute_now().isoformat(timespec="seconds"),
        eligibility_threshold=settings.attendance_eligibility_threshold_percent,
        fine_threshold=settings.attendance_fine_threshold_percent,
        drop_threshold=settings.attendance_drop_threshold_percent,
    )


# ---------------------------------------------------------------------------
# Lock preview
# ---------------------------------------------------------------------------
@router.get(
    "/offerings/{offering_id}/lock-preview", response_model=LockPreviewOut,
    summary="What locking this offering's attendance would do right now (read-only) -- admin only",
)
async def lock_preview(
    offering_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.ATTENDANCE_LOCK)),
    db=Depends(get_db),
) -> LockPreviewOut:
    return LockPreviewOut(**await AttendanceService(db).lock_preview(offering_id))


# ---------------------------------------------------------------------------
# Student: own history for one offering
# ---------------------------------------------------------------------------
@router.get(
    "/me/offerings/{offering_id}/records", response_model=list[MyAttendanceRecordOut],
    summary="My own class-by-class attendance for one course offering",
)
async def my_records(
    offering_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.ATTENDANCE_VIEW)),
    db=Depends(get_db),
) -> list[MyAttendanceRecordOut]:
    own_student_id = await _resolve_own_student_id(db, current_user)
    if own_student_id is None:
        raise AuthorizationError("This account is not linked to a student record.")
    rows = await AttendanceRepository(db).list_dated_for_student_in_offering(own_student_id, offering_id)
    return [
        MyAttendanceRecordOut(session_date=cs.session_date, start_time=cs.start_time, status=rec.status)
        for cs, rec in rows
    ]


# ---------------------------------------------------------------------------
# Register export (teacher of the offering, or admin)
# ---------------------------------------------------------------------------
@router.get(
    "/offerings/{offering_id}/report.csv",
    summary="Attendance register for one offering as CSV (students x classes, with totals) -- teacher of the offering, or admin",
)
async def attendance_report_csv(
    offering_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.ATTENDANCE_VIEW)),
    db=Depends(get_db),
):
    import csv
    import io

    await _assert_can_mark_or_view_offering(db, offering_id, current_user)
    repo = AttendanceRepository(db)
    sessions = await repo.list_taken_sessions_for_offering(offering_id)
    records = await repo.list_for_offering(offering_id)
    by_student: dict = {}
    for r in records:
        by_student.setdefault(r.student_id, {})[r.class_session_id] = r.status.value
    enrolled = await repo.list_enrolled_student_ids_for_offering(offering_id)
    student_ids = list(dict.fromkeys(list(enrolled) + list(by_student.keys())))
    students = [s for s in [await StudentRepository(db).get_by_id(sid) for sid in student_ids] if s is not None]
    students.sort(key=lambda s: (s.roll_number or ""))
    letter = {"PRESENT": "P", "ABSENT": "A", "LEAVE": "L"}
    service = AttendanceService(db)

    def safe(v):
        t = "" if v is None else str(v)
        return "\'" + t if t[:1] in ("=", "+", "-", "@") else t

    out = io.StringIO()
    w = safe_writer(out)
    w.writerow(["roll_number", "name"] + [str(cs.session_date) for cs in sessions] + ["attended", "counted", "percentage", "absent"])
    for st in students:
        row = by_student.get(st.id, {})
        pct = await service.compute_percentage(st.id, offering_id)
        w.writerow([safe(st.roll_number), safe(f"{st.first_name} {st.last_name}")]
                   + [letter.get(row.get(cs.id, ""), "") for cs in sessions]
                   + [pct["present_count"], pct["total_sessions_taken"], pct["percentage"], pct["absent_count"]])
    w.writerow([])
    w.writerow(["P=present", "A=absent"])
    return Response(
        content="\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="attendance_register.csv"'},
    )


# ---------------------------------------------------------------------------
# Correction requests (teacher -> admin)
# ---------------------------------------------------------------------------
async def _to_request_out(db, req) -> CorrectionRequestOut:
    from app.auth.models import User

    class_session = await ClassSessionRepository(db).get_by_id(req.class_session_id)
    offering = await CourseOfferingRepository(db).get_by_id(class_session.course_offering_id)
    course = await CourseRepository(db).get_by_id(offering.course_id)
    requester = None
    if req.requested_by is not None:
        employee = await EmployeeRepository(db).get_by_user_id(req.requested_by)
        if employee is not None:
            requester = f"{employee.first_name} {employee.last_name}"
        else:
            user = await db.get(User, req.requested_by)
            requester = user.username if user else None
    return CorrectionRequestOut(
        id=req.id, class_session_id=req.class_session_id, course_offering_id=offering.id,
        course_code=course.code if course else "?", course_title=course.title if course else "?",
        session_date=class_session.session_date, start_time=class_session.start_time, requested_by_name=requester,
        reason=req.reason, status=req.status, present_count=len(req.present_student_ids),
        decision_note=req.decision_note, created_at=req.created_at, decided_at=req.decided_at,
    )


@router.post(
    "/sessions/{class_session_id}/correction-request", response_model=CorrectionRequestOut, status_code=201,
    summary="Teacher: ask the admin to record/correct this class's attendance after the marking window closed",
)
async def create_correction_request(
    class_session_id: uuid.UUID,
    payload: CorrectionRequestCreate,
    current_user: CurrentUser = Depends(require_permission(Permissions.ATTENDANCE_MARK)),
    db=Depends(get_db),
) -> CorrectionRequestOut:
    class_session = await ClassSessionRepository(db).get_by_id(class_session_id)
    if class_session is None:
        raise ResourceNotFoundError("Class session not found.")
    await _assert_can_mark_or_view_offering(db, class_session.course_offering_id, current_user)
    req = await AttendanceService(db).create_correction_request(
        class_session_id, reason=payload.reason, present_student_ids=payload.present_student_ids, actor_id=current_user.id,
    )
    return await _to_request_out(db, req)


@router.get(
    "/correction-requests", response_model=list[CorrectionRequestOut],
    summary="Correction requests: all of them for the admin, only your own for a teacher",
)
async def list_correction_requests(
    status: str | None = None,
    current_user: CurrentUser = Depends(require_permission(Permissions.ATTENDANCE_MARK)),
    db=Depends(get_db),
) -> list[CorrectionRequestOut]:
    is_admin = bool({Permissions.ATTENDANCE_LOCK, Permissions.ATTENDANCE_CORRECTION_DECIDE} & set(current_user.permissions))   # admin or HOD sees all
    requests = await AttendanceService(db).list_correction_requests(
        status=status.upper() if status else None, requested_by=None if is_admin else current_user.id
    )
    out = [await _to_request_out(db, r) for r in requests]
    if is_admin:
        from app.core.scope import hod_department, teacher_ids_in

        dept = await hod_department(db, current_user)
        if dept:
            mine = await teacher_ids_in(db, dept)
            teacher_of = {o.id: o.assigned_teacher_id for o in await _offerings_by_ids(db, {r.course_offering_id for r in out})}
            out = [r for r in out if teacher_of.get(r.course_offering_id) in mine]
    return out


async def _offerings_by_ids(db, ids):
    from sqlalchemy import select as _select

    from app.academics.models import CourseOffering

    if not ids:
        return []
    return (await db.execute(_select(CourseOffering).where(CourseOffering.id.in_(list(ids))))).scalars().all()


@router.post(
    "/correction-requests/{request_id}/decision", response_model=CorrectionRequestOut,
    summary="Admin: approve (applies the proposed register) or reject a correction request",
)
async def decide_correction_request(
    request_id: uuid.UUID,
    payload: CorrectionDecision,
    current_user: CurrentUser = Depends(require_any_permission(Permissions.ATTENDANCE_LOCK, Permissions.ATTENDANCE_CORRECTION_DECIDE)),
    db=Depends(get_db),
) -> CorrectionRequestOut:
    from app.core.scope import hod_department, teacher_ids_in

    dept = await hod_department(db, current_user)
    if dept:
        from app.attendance.models import AttendanceCorrectionRequest as _ACR
        from app.core.exceptions import AuthorizationError

        pending = await db.get(_ACR, request_id)
        if pending is not None:
            cs = await ClassSessionRepository(db).get_by_id(pending.class_session_id)
            offering = await CourseOfferingRepository(db).get_by_id(cs.course_offering_id)
            if offering.assigned_teacher_id not in await teacher_ids_in(db, dept):
                raise AuthorizationError("This class belongs to another department.")
    req = await AttendanceService(db).decide_correction_request(
        request_id, approve=payload.approve, note=payload.note, actor_id=current_user.id
    )
    return await _to_request_out(db, req)

# ------------------------------------------------------------------------------------------------ approved leave
from datetime import date as _date  # noqa: E402

from pydantic import BaseModel as _BaseModel, Field as _Field  # noqa: E402


class LeaveIn(_BaseModel):
    from_date: _date
    to_date: _date
    reason: str = _Field(min_length=4, max_length=300)


@router.post("/students/{student_id}/leave", summary="Record approved leave (e.g. medical): those classes are not counted for or against attendance")
async def grant_leave(student_id: uuid.UUID, payload: LeaveIn,
                      current_user: CurrentUser = Depends(require_permission(Permissions.ATTENDANCE_LOCK)), db=Depends(get_db)) -> dict:
    return await AttendanceService(db).grant_leave(student_id, from_date=payload.from_date, to_date=payload.to_date, reason=payload.reason, actor_id=current_user.id)

# ---------------------------------------------------------------------------
# Graded attendance fines (app/attendance/shortfall.py)
# ---------------------------------------------------------------------------
class FinesRunIn(_BaseModel):
    term_label: str = _Field(min_length=1, max_length=50)
    confirm: bool = False                  # False = preview only


class ShortfallDecisionIn(_BaseModel):
    decision: str = _Field(pattern="^(DROP|FINE|EXCUSE)$")
    note: str = _Field(min_length=5, max_length=500)
    amount: float | None = _Field(default=None, gt=0, le=1_000_000)


@router.post("/fines/run", summary="Attendance fines for a term, by band: preview (confirm=false) or charge (confirm=true) -- admin only")
async def run_attendance_fines(payload: FinesRunIn, current_user: CurrentUser = Depends(require_permission(Permissions.ATTENDANCE_LOCK)),
                               db=Depends(get_db)) -> dict:
    from app.attendance.shortfall import ShortfallService

    return await ShortfallService(db).run(term_label=payload.term_label.strip(), actor_id=current_user.id, dry_run=not payload.confirm)


@router.get("/shortfalls", summary="Students short of attendance per subject (charged, awaiting a decision, decided) -- admin only")
async def list_shortfalls(term_label: str | None = None, status: str | None = None,
                          current_user: CurrentUser = Depends(require_permission(Permissions.ATTENDANCE_LOCK)), db=Depends(get_db)) -> list[dict]:
    from app.attendance.shortfall import ShortfallService

    return await ShortfallService(db).listing(term_label=term_label, status=status)


@router.post("/shortfalls/{shortfall_id}/decide", summary="Below the lowest band: drop from the course, allow with a fine, or excuse -- admin only")
async def decide_shortfall(shortfall_id: uuid.UUID, payload: ShortfallDecisionIn,
                           current_user: CurrentUser = Depends(require_permission(Permissions.ATTENDANCE_LOCK)), db=Depends(get_db)) -> dict:
    from app.attendance.shortfall import ShortfallService

    return await ShortfallService(db).decide(shortfall_id, decision=payload.decision, note=payload.note, amount=payload.amount, actor_id=current_user.id)
