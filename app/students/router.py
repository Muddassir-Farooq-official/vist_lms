from __future__ import annotations

from app.core.clock import institute_today

import uuid

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import Response

from app.auth.permissions import Permissions, Roles
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_permission
from app.core.exceptions import AuthorizationError, ResourceNotFoundError, ValidationAppError
from app.core.pagination import Page, page_params, PageParams
from app.core.storage import (
    ALLOWED_PDF_CONTENT_TYPES,
    ALLOWED_PHOTO_CONTENT_TYPES,
    FileTooLargeError,
    MAX_CERTIFICATE_PDF_BYTES,
    MAX_PHOTO_BYTES,
    SAFE_SERVE_HEADERS,
    UnsupportedFileTypeError,
    archive_file,
    delete_file,
    get_file_path,
    save_uploaded_file,
)
from app.students.models import Student, StudentStatus
from app.students.permissions import assert_can_view_student
from app.students.repository import StudentRepository
from app.students.schemas import (
    AdvanceSemesterRequest,
    AdvanceSemesterResponse,
    ComplaintSummary,
    EmergencyContactInfo,
    EnrollmentHistoryEntry,
    FeeSummary,
    FineSummary,
    GuardianInfo,
    IdentityCorrectionRequest,
    LibrarySummary,
    StudentAdminDetail,
    StudentCreatedResponse,
    StudentCreateRequest,
    StudentFullProfileResponse,
    StudentListItem,
    StudentSelfDetail,
    StudentStatusChangeRequest,
    StudentUpdateRequest,
)
from app.students.service import StudentService

router = APIRouter(prefix="/students", tags=["students"])


@router.post("", response_model=StudentCreatedResponse, status_code=201, summary="Manually create a student")
async def create_student(
    payload: StudentCreateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_CREATE)),
    db=Depends(get_db),
) -> StudentCreatedResponse:
    """Manual single-student creation (Section 4). The account is issued
    the institution's default password with a forced change on first
    login — see app/core/constants.py for the documented tradeoff."""
    student = await StudentService(db).create_student_manually(payload, actor_id=current_user.id)
    return StudentCreatedResponse(
        id=student.id,
        first_name=student.first_name,
        last_name=student.last_name,
        username=student._created_username,  # type: ignore[attr-defined]
        roll_number=student.roll_number,
        status=student.status,
    )


def _assert_directory_access(current_user: CurrentUser, *, allow_exam_department: bool = False) -> None:
    """Only staff who administer students may browse/export the directory.
    TEACHER / LIBRARIAN / STUDENT all hold student.view (for their own
    scoped screens) but must NOT be able to list every student: teachers
    use their offering rosters, librarians use /library/borrowers/search."""
    # The exam department, the principal and heads of department may SEARCH (to open a profile or result card, read-only); only the office exports.
    allowed = {Roles.ADMIN, Roles.ACCOUNTS_OFFICER} | ({Roles.EXAM_CONTROLLER, Roles.PRINCIPAL, Roles.HOD} if allow_exam_department else set())
    if not (allowed & set(current_user.roles)):
        raise AuthorizationError("You do not have access to the student directory.")


@router.get("", response_model=Page[StudentListItem], summary="Search/list students (paginated)")
async def list_students(
    q: str | None = None,
    program_id: uuid.UUID | None = None,
    status: StudentStatus | None = None,
    page: PageParams = Depends(page_params),
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_VIEW)),
    db=Depends(get_db),
) -> Page[StudentListItem]:
    """ADMIN/ACCOUNTS_OFFICER see the full institute; STUDENT role is
    rejected here (they use /students/me instead) — never given a search
    endpoint over other students' data."""
    _assert_directory_access(current_user, allow_exam_department=True)

    result = await StudentService(db).search_students(page=page, query=q, program_id=program_id, status=status)
    return Page[StudentListItem](
        items=[StudentListItem.model_validate(s) for s in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


async def _address_fields(db, student_id: uuid.UUID) -> dict:
    """Address fields live on a separate StudentAddress row, not on
    Student directly — merged in explicitly here rather than relying on
    an async-ORM lazy-loaded relationship (a known source of
    MissingGreenlet errors in SQLAlchemy's async mode outside careful
    eager-loading), same explicit-merge pattern already used
    successfully elsewhere in this codebase (Fines/Complaints
    "_to_detail" helpers)."""
    address = await StudentRepository(db).get_present_address(student_id)
    if address is None:
        return {"present_address": None, "province": None, "district": None, "city": None}
    return {
        "present_address": address.address_line,
        "province": address.province,
        "district": address.district,
        "city": address.city,
    }


async def _to_self_detail(db, student: Student) -> StudentSelfDetail:
    base = StudentSelfDetail.model_validate(student).model_dump()
    base.update(await _address_fields(db, student.id))
    return StudentSelfDetail(**base)


async def _to_admin_detail(db, student: Student) -> StudentAdminDetail:
    base = StudentAdminDetail.model_validate(student).model_dump()
    base.update(await _address_fields(db, student.id))
    return StudentAdminDetail(**base)


@router.get("/me", response_model=StudentSelfDetail, summary="Get my own student record")
async def get_my_record(
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> StudentSelfDetail:
    student = await StudentService(db).get_student_by_user_id(current_user.id)
    return await _to_self_detail(db, student)


def _csv_safe(value) -> str:
    """Neutralise spreadsheet formula injection (=, +, -, @ at cell start)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


@router.get("/export", summary="Download the (filtered) student list as CSV")
async def export_students_csv(
    q: str | None = None,
    program_id: uuid.UUID | None = None,
    status: StudentStatus | None = None,
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_VIEW)),
    db=Depends(get_db),
):
    import csv
    import io

    from fastapi.responses import Response

    from app.core.pagination import PageParams
    from app.programs.repository import ProgramRepository

    _assert_directory_access(current_user)
    repo = StudentRepository(db)
    program_names: dict = {}
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "roll_number", "first_name", "last_name", "cnic", "gender", "date_of_birth", "contact_no", "email",
        "blood_group", "program", "current_semester", "section", "status", "enrollment_date", "admission_session",
        "admission_category",
    ])
    page_no = 1
    while page_no <= 100:  # hard ceiling: 20,000 rows
        rows, _total = await repo.search(
            page=PageParams(page=page_no, page_size=200), query=q, program_id=program_id, status=status
        )
        if not rows:
            break
        for st in rows:
            if st.program_id and st.program_id not in program_names:
                prog = await ProgramRepository(db).get_by_id(st.program_id)
                program_names[st.program_id] = prog.code if prog else ""
            writer.writerow([_csv_safe(v) for v in [
                st.roll_number, st.first_name, st.last_name, st.cnic, st.gender, st.date_of_birth, st.contact_no,
                st.email, st.blood_group, program_names.get(st.program_id, ""), st.current_semester, st.section_label,
                st.status.value, st.enrollment_date, st.admission_session, st.admission_category,
            ]])
        page_no += 1
    return Response(
        content="\ufeff" + out.getvalue(),  # BOM so Excel opens UTF-8 correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="students.csv"'},
    )


@router.get("/{student_id}/admission-slip", summary="Printable admission slip with login details")
async def admission_slip(
    student_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_UPDATE)),
    db=Depends(get_db),
):
    """One page the registrar prints and hands to the student. Shows the
    temporary password ONLY while the account still has
    must_change_password=True (i.e. the student has not logged in yet) --
    afterwards the slip shows the username only, never a stale password."""
    import html

    from fastapi.responses import HTMLResponse

    from app.auth.models import User
    from app.programs.repository import ProgramRepository

    student = await StudentService(db).get_student(student_id)
    program = await ProgramRepository(db).get_by_id(student.program_id) if student.program_id else None
    user = await db.get(User, student.user_id) if student.user_id else None
    show_password = bool(user and user.must_change_password and user.temp_password)
    e = html.escape
    password_row = (
        f"<tr><th>Temporary password</th><td><b>{e(user.temp_password or "")}</b> (change at first login)</td></tr>"
        if show_password
        else "<tr><th>Password</th><td>Already changed by the student</td></tr>"
    )
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Admission Slip {e(student.roll_number or '')}</title>
<style>body{{font-family:Arial,sans-serif;margin:40px}}h1{{margin:0}}table{{border-collapse:collapse;margin-top:20px;width:100%}}
th,td{{border:1px solid #444;padding:8px;text-align:left}}th{{width:35%;background:#eee}}.small{{color:#555;font-size:12px;margin-top:24px}}
@media print{{.noprint{{display:none}}}}</style></head><body>
<h1>Vertex Institute of Science &amp; Technology</h1><h3>Admission Slip</h3>
<table>
<tr><th>Name</th><td>{e(student.first_name)} {e(student.last_name)}</td></tr>
<tr><th>Roll number / Login username</th><td><b>{e(student.roll_number or '')}</b></td></tr>
<tr><th>CNIC</th><td>{e(student.cnic)}</td></tr>
<tr><th>Program</th><td>{e(program.name if program else '-')}</td></tr>
<tr><th>Semester</th><td>{e(str(student.current_semester or '-'))}</td></tr>
<tr><th>Section</th><td>{e(student.section_label or '-')}</td></tr>
<tr><th>Admission session</th><td>{e(student.admission_session or '-')}</td></tr>
<tr><th>Enrollment date</th><td>{e(str(student.enrollment_date or '-'))}</td></tr>
{password_row}
</table>
<p class="small">Keep these details private. Log in at the student portal with your roll number.</p>
<p class="noprint"><button onclick="window.print()">Print</button></p></body></html>"""
    return HTMLResponse(page)


@router.get("/{student_id}", response_model=StudentAdminDetail, summary="Get full student detail (admin)")
async def get_student(
    student_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_VIEW)),
    db=Depends(get_db),
) -> StudentAdminDetail:
    student = await StudentService(db).get_student(student_id)
    assert_can_view_student(current_user, student)
    return await _to_admin_detail(db, student)


@router.patch("/{student_id}", response_model=StudentAdminDetail, summary="Edit a student's editable fields")
async def update_student(
    student_id: uuid.UUID,
    payload: StudentUpdateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_UPDATE)),
    db=Depends(get_db),
) -> StudentAdminDetail:
    student = await StudentService(db).update_student(student_id, payload, actor_id=current_user.id)
    return await _to_admin_detail(db, student)


@router.post("/{student_id}/identity", response_model=StudentAdminDetail,
             summary="Correct a mistyped CNIC or the guardian's details (reason required; a changed guardian CNIC cuts the old parent login)")
async def correct_identity(
    student_id: uuid.UUID,
    payload: IdentityCorrectionRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_UPDATE)),
    db=Depends(get_db),
) -> StudentAdminDetail:
    student = await StudentService(db).correct_identity(student_id, payload, actor_id=current_user.id)
    return await _to_admin_detail(db, student)


@router.post("/{student_id}/status", response_model=StudentAdminDetail, summary="Change student status")
async def change_status(
    student_id: uuid.UUID,
    payload: StudentStatusChangeRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_UPDATE)),
    db=Depends(get_db),
) -> StudentAdminDetail:
    student = await StudentService(db).change_status(
        student_id, new_status=payload.new_status, reason=payload.reason, actor_id=current_user.id
    )
    return await _to_admin_detail(db, student)


@router.post("/{student_id}/reset-password", status_code=204, summary="Reset a student's password to the default")
async def reset_student_password(
    student_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_UPDATE)),
    db=Depends(get_db),
) -> None:
    await StudentService(db).reset_password(student_id, actor_id=current_user.id)


@router.post(
    "/{student_id}/reactivate-after-attendance-fine", status_code=204,
    summary="Reactivate a login blocked by an unpaid attendance fine — refuses if the fine is still unpaid",
)
async def reactivate_after_attendance_fine(
    student_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_UPDATE)),
    db=Depends(get_db),
) -> None:
    await StudentService(db).reactivate_after_attendance_fine(student_id, actor_id=current_user.id)


@router.post(
    "/advance-semester",
    response_model=AdvanceSemesterResponse,
    summary="Bulk-advance current_semester by 1 for matching active students (end-of-term action)",
)
async def advance_semester_bulk(
    payload: AdvanceSemesterRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_UPDATE)),
    db=Depends(get_db),
) -> AdvanceSemesterResponse:
    result = await StudentService(db).advance_semester_bulk(payload, actor_id=current_user.id)
    return AdvanceSemesterResponse(**result)


# ---------------------------------------------------------------------------
# Profile photo / certificate PDF — self-service ("me") and admin ("{id}")
# ---------------------------------------------------------------------------
async def _resolve_own_student(db, current_user: CurrentUser) -> Student:
    student = await StudentRepository(db).get_by_user_id(current_user.id)
    if student is None:
        raise AuthorizationError("This account is not linked to a student record.")
    return student


async def _upload_photo(db, student: Student, file: UploadFile, actor_id: uuid.UUID) -> StudentAdminDetail:
    content = await file.read()
    try:
        storage_key, safe_name = save_uploaded_file(
            category="student-photos", entity_id=student.id, content=content,
            original_filename=file.filename or "photo.jpg", content_type=file.content_type or "",
            allowed_content_types=ALLOWED_PHOTO_CONTENT_TYPES, max_bytes=MAX_PHOTO_BYTES,
        )
    except (UnsupportedFileTypeError, FileTooLargeError) as exc:
        raise ValidationAppError(str(exc)) from exc

    kept = archive_file(student.photo_storage_key)
    if kept:
        from app.audit.service import AuditService

        await AuditService(db).log(actor_user_id=actor_id, action="student.photo_replaced", entity_type="student", entity_id=student.id,
                                   before={"archived_as": kept, "name": student.photo_original_name}, after={"name": safe_name})
    student.photo_storage_key = storage_key
    student.photo_original_name = safe_name
    return await _to_admin_detail(db, student)


async def _upload_certificate(db, student: Student, file: UploadFile, actor_id: uuid.UUID) -> StudentAdminDetail:
    content = await file.read()
    try:
        storage_key, safe_name = save_uploaded_file(
            category="student-certificates", entity_id=student.id, content=content,
            original_filename=file.filename or "certificates.pdf", content_type=file.content_type or "",
            allowed_content_types=ALLOWED_PDF_CONTENT_TYPES, max_bytes=MAX_CERTIFICATE_PDF_BYTES,
        )
    except (UnsupportedFileTypeError, FileTooLargeError) as exc:
        raise ValidationAppError(str(exc)) from exc

    kept = archive_file(student.certificate_storage_key)
    if kept:
        from app.audit.service import AuditService

        await AuditService(db).log(actor_user_id=actor_id, action="student.certificate_replaced", entity_type="student", entity_id=student.id,
                                   before={"archived_as": kept, "name": student.certificate_original_name}, after={"name": safe_name})
    student.certificate_storage_key = storage_key
    student.certificate_original_name = safe_name
    return await _to_admin_detail(db, student)


def _serve_file(storage_key: str | None, original_name: str | None, media_type: str) -> Response:
    path = get_file_path(storage_key)
    if path is None:
        raise ResourceNotFoundError("No file uploaded.")
    filename = original_name or path.name
    headers = {"Content-Disposition": f'inline; filename="{filename}"', **SAFE_SERVE_HEADERS}
    return Response(content=path.read_bytes(), media_type=media_type, headers=headers)


@router.post("/me/photo", response_model=StudentAdminDetail, summary="Upload my own profile photo")
async def upload_my_photo(
    file: UploadFile, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db),
) -> StudentAdminDetail:
    student = await _resolve_own_student(db, current_user)
    return await _upload_photo(db, student, file, current_user.id)


@router.get("/me/photo", summary="Download my own profile photo")
async def get_my_photo(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    student = await _resolve_own_student(db, current_user)
    return _serve_file(student.photo_storage_key, student.photo_original_name, "image/jpeg")


@router.delete("/me/photo", status_code=204, summary="Remove my own profile photo")
async def delete_my_photo(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> None:
    student = await _resolve_own_student(db, current_user)
    delete_file(student.photo_storage_key)
    student.photo_storage_key = None
    student.photo_original_name = None


@router.post("/me/certificate", response_model=StudentAdminDetail, summary="Upload my own combined certificate PDF")
async def upload_my_certificate(
    file: UploadFile, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db),
) -> StudentAdminDetail:
    student = await _resolve_own_student(db, current_user)
    return await _upload_certificate(db, student, file, current_user.id)


@router.get("/me/certificate", summary="Download my own combined certificate PDF")
async def get_my_certificate(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    student = await _resolve_own_student(db, current_user)
    return _serve_file(student.certificate_storage_key, student.certificate_original_name, "application/pdf")


@router.delete("/me/certificate", status_code=204, summary="Remove my own combined certificate PDF")
async def delete_my_certificate(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> None:
    student = await _resolve_own_student(db, current_user)
    delete_file(student.certificate_storage_key)
    student.certificate_storage_key = None
    student.certificate_original_name = None


@router.post("/{student_id}/photo", response_model=StudentAdminDetail, summary="Upload a student's profile photo (admin)")
async def admin_upload_photo(
    student_id: uuid.UUID, file: UploadFile,
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_UPDATE)), db=Depends(get_db),
) -> StudentAdminDetail:
    student = await StudentService(db).get_student(student_id)
    return await _upload_photo(db, student, file, current_user.id)


@router.get("/{student_id}/photo", summary="Download a student's profile photo (staff)")
async def admin_get_photo(
    student_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_VIEW)), db=Depends(get_db),
) -> Response:
    student = await StudentService(db).get_student(student_id)
    assert_can_view_student(current_user, student)
    return _serve_file(student.photo_storage_key, student.photo_original_name, "image/jpeg")


@router.delete("/{student_id}/photo", status_code=204, summary="Remove a student's profile photo (admin)")
async def admin_delete_photo(
    student_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_UPDATE)), db=Depends(get_db),
) -> None:
    student = await StudentService(db).get_student(student_id)
    delete_file(student.photo_storage_key)
    student.photo_storage_key = None
    student.photo_original_name = None


@router.post("/{student_id}/certificate", response_model=StudentAdminDetail, summary="Upload a student's combined certificate PDF (admin)")
async def admin_upload_certificate(
    student_id: uuid.UUID, file: UploadFile,
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_UPDATE)), db=Depends(get_db),
) -> StudentAdminDetail:
    student = await StudentService(db).get_student(student_id)
    return await _upload_certificate(db, student, file, current_user.id)


@router.get("/{student_id}/certificate", summary="Download a student's combined certificate PDF (staff)")
async def admin_get_certificate(
    student_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_VIEW)), db=Depends(get_db),
) -> Response:
    student = await StudentService(db).get_student(student_id)
    assert_can_view_student(current_user, student)
    return _serve_file(student.certificate_storage_key, student.certificate_original_name, "application/pdf")


@router.delete("/{student_id}/certificate", status_code=204, summary="Remove a student's combined certificate PDF (admin)")
async def admin_delete_certificate(
    student_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_UPDATE)), db=Depends(get_db),
) -> None:
    student = await StudentService(db).get_student(student_id)
    delete_file(student.certificate_storage_key)
    student.certificate_storage_key = None
    student.certificate_original_name = None


# ---------------------------------------------------------------------------
# Full profile — the "everything about this student in one place" view.
# Deliberately built at the router layer, not inside StudentService —
# StudentService has no dependency on Fees/Library/Fines/Complaints/
# Academics, and this aggregation is the one place that legitimately
# needs all of them, so it stays here rather than pulling every other
# module into the core Students service.
# ---------------------------------------------------------------------------
@router.get(
    "/{student_id}/full-profile", response_model=StudentFullProfileResponse,
    summary="Everything about this student in one place — personal, academic, financial, library, fines, complaints",
)
async def get_student_full_profile(
    student_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_VIEW)),
    db=Depends(get_db),
) -> StudentFullProfileResponse:
    student = await StudentService(db).get_student(student_id)
    assert_can_view_student(current_user, student)

    address_fields = await _address_fields(db, student_id)

    program_name = None
    if student.program_id is not None:
        from app.programs.models import Program

        program = await db.get(Program, student.program_id)
        program_name = program.name if program else None

    # Guardians + emergency contacts — direct queries, not relationships
    # (see app/academics/router.py's fix history for why relationship
    # attribute access on ORM objects is avoided throughout this codebase
    # in async context).
    from sqlalchemy import select

    from app.students.models import StudentEmergencyContact, StudentGuardian

    guardian_rows = (
        await db.execute(select(StudentGuardian).where(StudentGuardian.student_id == student_id))
    ).scalars().all()
    guardians = [
        GuardianInfo(name=g.name, cnic=g.cnic, contact_no=g.contact_no, relation=g.relation)
        for g in guardian_rows
    ]

    emergency_rows = (
        await db.execute(select(StudentEmergencyContact).where(StudentEmergencyContact.student_id == student_id))
    ).scalars().all()
    emergency_contacts = [
        EmergencyContactInfo(name=e.name, contact_no=e.contact_no, relation=e.relation) for e in emergency_rows
    ]

    # Qualifications
    from app.profiles.models import ProfilePartyType
    from app.profiles.schemas import QualificationOut as _QualOut
    from app.profiles.service import QualificationService

    quals = await QualificationService(db).list_for_party(ProfilePartyType.STUDENT, student_id)
    qualifications = [_QualOut.model_validate(q) for q in quals]

    # Academic enrollment history
    from app.academics.models import Course, CourseOffering
    from app.academics.service import EnrollmentService

    enrollment_rows = await EnrollmentService(db).list_for_student(student_id)
    enrollments = []
    for e in enrollment_rows:
        offering = await db.get(CourseOffering, e.course_offering_id)
        course = await db.get(Course, offering.course_id) if offering else None
        enrollments.append(
            EnrollmentHistoryEntry(
                course_code=course.code if course else "Unknown",
                course_title=course.title if course else "Unknown",
                credit_hours=course.credit_hours if course else 0,
                term_label=offering.term_label if offering else "Unknown",
                status=e.status.value, outcome=e.outcome.value if e.outcome else None, is_retake=e.is_retake,
            )
        )

    # Fee summary
    from app.fees.models import LedgerEntryType, PayerType
    from app.fees.repository import FeeAssignmentRepository

    fee_repo = FeeAssignmentRepository(db)
    assignments = await fee_repo.list_for_payer(PayerType.STUDENT, student_id)
    total_due, total_paid = 0.0, 0.0
    for assignment in assignments:
        ledger = await fee_repo.get_ledger(assignment.id)
        for entry in ledger:
            if entry.entry_type == LedgerEntryType.DEBIT:
                total_due += float(entry.amount)
            elif entry.entry_type == LedgerEntryType.CONCESSION:
                total_due -= float(entry.amount)       # a scholarship reduces what is owed; it is not a payment
            else:
                total_paid += float(entry.amount)
    fee_summary = FeeSummary(
        total_assignments=len(assignments), total_due=total_due, total_paid=total_paid,
        total_outstanding=max(0.0, total_due - total_paid),
    )

    # Library summary
    from app.library.models import BorrowerType, IssueStatus
    from app.library.repository import BookIssueRepository

    library_repo = BookIssueRepository(db)
    active_loans = await library_repo.list_active_for_borrower(BorrowerType.STUDENT, student_id)
    history_loans = await library_repo.list_history_for_borrower(BorrowerType.STUDENT, student_id)
    overdue_loans = [loan for loan in active_loans if loan.status == IssueStatus.ISSUED and loan.due_date < institute_today()]
    library_summary = LibrarySummary(
        active_loans=len(active_loans), overdue_loans=len(overdue_loans), past_loans=len(history_loans)
    )

    # Fine summary
    from app.fines.models import FinePayerType
    from app.fines.repository import FineRepository

    fine_balance = await FineRepository(db).balance_for(FinePayerType.STUDENT, student_id)
    fine_summary = FineSummary(
        total_pending=fine_balance["total_pending"], total_paid=fine_balance["total_paid"],
        total_waived=fine_balance["total_waived"],
    )

    # Complaint summary
    from app.complaints.models import ComplaintPartyType
    from app.complaints.repository import ComplaintRepository

    complaint_repo = ComplaintRepository(db)
    filed_by = await complaint_repo.list_for_complainant(ComplaintPartyType.STUDENT, student_id)
    filed_against = await complaint_repo.list_all(target_type=ComplaintPartyType.STUDENT, target_id=student_id)
    complaint_summary = ComplaintSummary(filed_by_them=len(filed_by), filed_against_them=len(filed_against))

    return StudentFullProfileResponse(
        id=student.id, first_name=student.first_name, last_name=student.last_name, roll_number=student.roll_number,
        cnic=student.cnic, contact_no=student.contact_no, date_of_birth=student.date_of_birth, gender=student.gender,
        marital_status=student.marital_status, religion=student.religion, nationality=student.nationality,
        status=student.status, photo_original_name=student.photo_original_name, has_photo=student.has_photo,
        certificate_original_name=student.certificate_original_name, has_certificate=student.has_certificate,
        program_id=student.program_id, program_name=program_name, current_semester=student.current_semester,
        enrollment_date=student.enrollment_date, admission_fee_paid=student.admission_fee_paid,
        email=student.email, blood_group=student.blood_group,
        admission_session=student.admission_session, admission_category=student.admission_category,
        section_label=student.section_label,
        present_address=address_fields["present_address"], province=address_fields["province"],
        district=address_fields["district"], city=address_fields["city"],
        guardians=guardians, emergency_contacts=emergency_contacts,
        qualifications=qualifications, enrollments=enrollments,
        fees=fee_summary, library=library_summary, fines=fine_summary, complaints=complaint_summary,
    )