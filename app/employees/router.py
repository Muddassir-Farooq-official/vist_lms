from __future__ import annotations

from app.core.clock import institute_today

import uuid

from fastapi import APIRouter, Depends, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.permissions import Permissions, Roles
from app.auth.repository import AuthRepository
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
    delete_file,
    get_file_path,
    save_uploaded_file,
)
from app.employees.models import Employee, EmployeeStatus, JobRole
from app.employees.repository import EmployeeRepository
from app.employees.schemas import (
    BonusCreateRequest,
    BonusEntry,
    BonusOut,
    EmployeeComplaintSummary,
    EmployeeClearanceOut,
    EmployeeCreatedResponse,
    EmployeeCreateRequest,
    EmployeeDetail,
    EmployeeFeeSummary,
    EmployeeFineSummary,
    EmployeeFullProfileResponse,
    EmployeeLibrarySummary,
    EmployeeListItem,
    EmployeePasswordResetRequest,
    EmployeeSelfDetail,
    EmployeeStatusChangeRequest,
    EmployeeUpdateRequest,
    JobRoleChangeRequest,
    JobRoleCreate,
    JobRoleOut,
    JobRoleUpdate,
    SalaryHistoryEntry,
    SalaryHistoryOut,
    SalaryIncrementRequest,
    TeachingSummary,
)
from app.employees.service import EmployeeService, JobRoleService

router = APIRouter(prefix="/employees", tags=["employees"])


async def _to_detail(db, employee: Employee) -> EmployeeDetail:
    """Populates the username, which lives on the linked User row, not on
    Employee itself — a plain model_validate(employee) can't see it. This
    is the single place that resolves it, so every endpoint returning
    EmployeeDetail shows it consistently (this used to be missing
    entirely, which is exactly what caused a real "wrong username" login
    confusion — the only place username was ever shown was a one-time
    creation message, with no way to look it up again afterward)."""
    detail = EmployeeDetail.model_validate(employee)
    if employee.user_id is not None:
        user = await AuthRepository(db).get_user_by_id(employee.user_id)
        detail.username = user.username if user else None
    return detail
job_roles_router = APIRouter(prefix="/job-roles", tags=["employees"])


@job_roles_router.get("", response_model=list[JobRoleOut], summary="List job roles")
async def list_job_roles(
    active_only: bool = False,
    current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_VIEW)),
    db=Depends(get_db),
) -> list[JobRoleOut]:
    roles = await JobRoleService(db).list_all(active_only=active_only)
    return [JobRoleOut.model_validate(r) for r in roles]


@job_roles_router.post("", response_model=JobRoleOut, status_code=201, summary="Create a job role")
async def create_job_role(
    payload: JobRoleCreate,
    current_user: CurrentUser = Depends(require_permission(Permissions.JOB_ROLE_MANAGE)),
    db=Depends(get_db),
) -> JobRoleOut:
    role = await JobRoleService(db).create(payload, actor_id=current_user.id)
    return JobRoleOut.model_validate(role)


@job_roles_router.patch("/{job_role_id}", response_model=JobRoleOut, summary="Edit a job role (title, access role, active status)")
async def update_job_role(
    job_role_id: uuid.UUID,
    payload: JobRoleUpdate,
    current_user: CurrentUser = Depends(require_permission(Permissions.JOB_ROLE_MANAGE)),
    db=Depends(get_db),
) -> JobRoleOut:
    role = await JobRoleService(db).update(job_role_id, payload, actor_id=current_user.id)
    return JobRoleOut.model_validate(role)


@job_roles_router.delete(
    "/{job_role_id}",
    status_code=204,
    summary="Delete a job role (only if no employees are assigned to it)",
)
async def delete_job_role(
    job_role_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.JOB_ROLE_MANAGE)),
    db=Depends(get_db),
) -> None:
    await JobRoleService(db).delete(job_role_id, actor_id=current_user.id)


@router.get("", response_model=Page[EmployeeListItem], summary="Search/list employees (paginated)")
async def list_employees(
    q: str | None = None,
    status: EmployeeStatus | None = None,
    job_role_id: uuid.UUID | None = None,
    role: str | None = None,
    page: PageParams = Depends(page_params),
    current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_VIEW)),
    db=Depends(get_db),
) -> Page[EmployeeListItem]:
    result = await EmployeeService(db).search_employees(
        page=page, query=q, status=status, job_role_id=job_role_id, role_code=role
    )
    titles = {j.id: j.title for j in (await db.execute(select(JobRole).where(JobRole.id.in_({e.job_role_id for e in result.items})))).scalars()} if result.items else {}

    def row(e) -> EmployeeListItem:
        item = EmployeeListItem.model_validate(e)
        item.job_title = titles.get(e.job_role_id)
        return item

    return Page[EmployeeListItem](
        items=[row(e) for e in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.post("", response_model=EmployeeCreatedResponse, status_code=201, summary="Create an employee")
async def create_employee(
    payload: EmployeeCreateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_CREATE)),
    db=Depends(get_db),
) -> EmployeeCreatedResponse:
    employee = await EmployeeService(db).create_employee(payload, actor_id=current_user.id)
    return EmployeeCreatedResponse(
        id=employee.id,
        first_name=employee.first_name,
        last_name=employee.last_name,
        employee_code=employee.employee_code,
        username=employee._created_username,  # type: ignore[attr-defined]
        status=employee.status,
    )


@router.get("/me", response_model=EmployeeSelfDetail, summary="Get my own employee record")
async def get_my_employee_record(
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
) -> EmployeeSelfDetail:
    employee = await EmployeeService(db).get_employee_by_user_id(current_user.id)
    return EmployeeSelfDetail.model_validate(employee)


def _csv_safe(value) -> str:
    """Neutralise spreadsheet formula injection (=, +, -, @ at cell start)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


@router.get("/export", summary="Download the (filtered) employee list as CSV")
async def export_employees_csv(
    q: str | None = None,
    status: EmployeeStatus | None = None,
    job_role_id: uuid.UUID | None = None,
    current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_VIEW)),
    db=Depends(get_db),
):
    import csv
    import io

    repo = EmployeeRepository(db)
    role_titles = {jr.id: jr.title for jr in (await db.execute(select(JobRole))).scalars()}
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "employee_code", "first_name", "last_name", "cnic", "gender", "date_of_birth", "contact_no", "email",
        "blood_group", "marital_status", "job_role", "employment_type", "department", "hire_date", "status",
        "emergency_contact_name", "emergency_contact_no", "province", "district", "city",
    ])
    page_no = 1
    while page_no <= 100:
        rows, _total = await repo.search(
            page=PageParams(page=page_no, page_size=200), query=q, status=status, job_role_id=job_role_id
        )
        if not rows:
            break
        for e in rows:
            writer.writerow([_csv_safe(v) for v in [
                e.employee_code, e.first_name, e.last_name, e.cnic, e.gender, e.date_of_birth, e.contact_no,
                e.email, e.blood_group, e.marital_status, role_titles.get(e.job_role_id, ""), e.employment_type,
                e.department, e.hire_date, e.status.value, e.emergency_contact_name, e.emergency_contact_no,
                e.province, e.district, e.city,
            ]])
        page_no += 1
    return Response(
        content="\ufeff" + out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="employees.csv"'},
    )


_IMPORT_REQUIRED = (
    "first_name", "last_name", "date_of_birth", "gender", "cnic", "contact_no", "hire_date", "job_role_code",
    "base_salary",
)
_IMPORT_OPTIONAL = (
    "department", "email", "blood_group", "marital_status", "employment_type", "emergency_contact_name",
    "emergency_contact_no", "religion", "province", "district", "city", "address_line",
)
_IMPORT_MAX_ROWS = 500


@router.get("/import-template", summary="Download the CSV template for bulk staff import")
async def employee_import_template(
    current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_CREATE)),
):
    example = {
        "first_name": "Ayesha", "last_name": "Khan", "date_of_birth": "1988-04-12", "gender": "Female",
        "cnic": "35202-1234567-1", "contact_no": "03001234567", "hire_date": "2026-08-01",
        "job_role_code": "LECT", "base_salary": "85000", "department": "Computer Science",
        "employment_type": "PERMANENT",
    }
    header = ",".join(_IMPORT_REQUIRED + _IMPORT_OPTIONAL)
    row = ",".join(example.get(h, "") for h in _IMPORT_REQUIRED + _IMPORT_OPTIONAL)
    return Response(
        content=header + "\n" + row + "\n", media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="employee_import_template.csv"'},
    )


@router.post("/import", summary="Bulk-create employees from a CSV (all rows valid, or nothing is created)")
async def import_employees(
    file: UploadFile,
    initial_password: str = Form(min_length=10, max_length=256),
    current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_CREATE)),
    db=Depends(get_db),
):
    """Existing staff must be loaded in one go on launch day. Same rule as the
    student import: if ANY row is invalid, NOTHING is imported and every
    problem is reported by row number. All imported staff get the one
    `initial_password` and must change it at first login."""
    import csv
    import io

    from pydantic import ValidationError

    from app.core.config import settings
    from app.imports.validators import _parse_date

    raw = await file.read()
    if len(raw) > settings.import_max_file_size_mb * 1024 * 1024:
        raise ValidationAppError("File is too large.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationAppError("File must be UTF-8 encoded CSV.") from exc

    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    missing = [h for h in _IMPORT_REQUIRED if h not in headers]
    if missing:
        raise ValidationAppError("Missing required column(s): " + ", ".join(missing), details={"missing": missing})
    unknown = [h for h in headers if h not in _IMPORT_REQUIRED + _IMPORT_OPTIONAL]
    if unknown:
        raise ValidationAppError("Unknown column(s): " + ", ".join(unknown), details={"unknown": unknown})

    job_roles = {jr.code.upper(): jr for jr in (await db.execute(select(JobRole))).scalars()}
    repo = EmployeeRepository(db)
    errors: list[dict] = []
    parsed: list[tuple[int, EmployeeCreateRequest]] = []
    seen_cnics: set[str] = set()

    for line_no, row in enumerate(reader, start=2):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if not any(row.values()):
            continue
        if len(parsed) + len(errors) >= _IMPORT_MAX_ROWS:
            raise ValidationAppError(f"A single import is limited to {_IMPORT_MAX_ROWS} rows.")
        row_errors: list[str] = []
        job_role = job_roles.get(row.get("job_role_code", "").upper())
        if job_role is None:
            row_errors.append(f"job_role_code '{row.get('job_role_code', '')}' does not exist")
        elif not job_role.is_active:
            row_errors.append(f"job role '{job_role.code}' is deactivated")
        dob = _parse_date(row.get("date_of_birth", "")) if row.get("date_of_birth") else None
        hire = _parse_date(row.get("hire_date", "")) if row.get("hire_date") else None
        if dob is None:
            row_errors.append("date_of_birth must be YYYY-MM-DD or DD/MM/YYYY")
        if hire is None:
            row_errors.append("hire_date must be YYYY-MM-DD or DD/MM/YYYY")
        try:
            salary = float(row.get("base_salary", ""))
        except ValueError:
            salary = None
            row_errors.append("base_salary must be a number")
        request = None
        if not row_errors:
            payload = {k: (v or None) for k, v in row.items() if k not in ("job_role_code", "base_salary")}
            payload.update(
                date_of_birth=dob, hire_date=hire, job_role_id=job_role.id, base_salary=salary,
                password=initial_password,
            )
            for required_text in ("first_name", "last_name", "gender", "cnic", "contact_no"):
                payload[required_text] = row.get(required_text, "")
            try:
                request = EmployeeCreateRequest(**payload)
            except ValidationError as exc:
                for err in exc.errors():
                    row_errors.append(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}")
        if request is not None:
            if request.cnic in seen_cnics:
                row_errors.append(f"CNIC {request.cnic} appears more than once in this file")
            elif await repo.get_by_cnic(request.cnic):
                row_errors.append(f"an employee with CNIC {request.cnic} already exists")
            seen_cnics.add(request.cnic)
        if row_errors:
            errors.append({"row": line_no, "errors": row_errors})
        elif request is not None:
            parsed.append((line_no, request))

    if errors:
        raise ValidationAppError(
            f"{len(errors)} row(s) have problems. Nothing was imported -- fix them and upload again.",
            details={"rows": errors[:100]},
        )
    if not parsed:
        raise ValidationAppError("The file contains no employee rows.")

    service = EmployeeService(db)
    created = []
    for _line, request in parsed:
        employee = await service.create_employee(request, actor_id=current_user.id)
        created.append({"employee_code": employee.employee_code, "name": f"{employee.first_name} {employee.last_name}"})
    return {"imported": len(created), "employees": created}


@router.post("/{employee_id}/job-role", response_model=EmployeeDetail, summary="Change an employee's job role (promotion / transfer)")
async def change_employee_job_role(
    employee_id: uuid.UUID,
    payload: JobRoleChangeRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.JOB_ROLE_MANAGE)),
    db=Depends(get_db),
) -> EmployeeDetail:
    employee = await EmployeeService(db).change_job_role(
        employee_id, payload.new_job_role_id, reason=payload.reason, actor_id=current_user.id
    )
    return await _to_detail(db, employee)


@router.get("/{employee_id}/clearance", response_model=EmployeeClearanceOut, summary="What is still attached to this employee (teaching, loans, fines, dues)")
async def employee_clearance(
    employee_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_VIEW)),
    db=Depends(get_db),
) -> EmployeeClearanceOut:
    return EmployeeClearanceOut(**await EmployeeService(db).clearance(employee_id))


@router.get("/{employee_id}", response_model=EmployeeDetail, summary="Get employee detail")
async def get_employee(
    employee_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_VIEW)),
    db=Depends(get_db),
) -> EmployeeDetail:
    employee = await EmployeeService(db).get_employee(employee_id)
    return await _to_detail(db, employee)


@router.patch("/{employee_id}", response_model=EmployeeDetail, summary="Update employee profile fields (admin)")
async def update_employee(
    employee_id: uuid.UUID,
    payload: EmployeeUpdateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_UPDATE)),
    db=Depends(get_db),
) -> EmployeeDetail:
    employee = await EmployeeService(db).update_employee(employee_id, payload, actor_id=current_user.id)
    return await _to_detail(db, employee)


@router.post("/{employee_id}/status", response_model=EmployeeDetail, summary="Change employee status")
async def change_employee_status(
    employee_id: uuid.UUID,
    payload: EmployeeStatusChangeRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_UPDATE)),
    db=Depends(get_db),
) -> EmployeeDetail:
    employee = await EmployeeService(db).change_status(
        employee_id, new_status=payload.new_status, reason=payload.reason, actor_id=current_user.id
    )
    return await _to_detail(db, employee)


@router.post("/{employee_id}/reset-password", status_code=204, summary="Set a new password for an employee")
async def reset_employee_password(
    employee_id: uuid.UUID,
    payload: EmployeePasswordResetRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_UPDATE)),
    db=Depends(get_db),
) -> None:
    await EmployeeService(db).reset_password(employee_id, new_password=payload.new_password, actor_id=current_user.id)


@router.get(
    "/{employee_id}/salary-history", response_model=list[SalaryHistoryOut], summary="Get salary history"
)
async def get_salary_history(
    employee_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_VIEW)),
    db=Depends(get_db),
) -> list[SalaryHistoryOut]:
    from app.employees.repository import EmployeeRepository

    rows = await EmployeeRepository(db).get_salary_history(employee_id)
    return [SalaryHistoryOut.model_validate(r) for r in rows]


@router.post(
    "/{employee_id}/salary-increment", response_model=SalaryHistoryOut, summary="Apply a manual salary increment"
)
async def apply_salary_increment(
    employee_id: uuid.UUID,
    payload: SalaryIncrementRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.SALARY_MANAGE)),
    db=Depends(get_db),
) -> SalaryHistoryOut:
    row = await EmployeeService(db).apply_salary_increment(employee_id, payload, actor_id=current_user.id)
    return SalaryHistoryOut.model_validate(row)


@router.get("/{employee_id}/bonuses", response_model=list[BonusOut], summary="List bonuses for an employee")
async def list_bonuses(
    employee_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_VIEW)),
    db=Depends(get_db),
) -> list[BonusOut]:
    from app.employees.repository import EmployeeRepository

    rows = await EmployeeRepository(db).get_bonuses(employee_id)
    return [BonusOut.model_validate(r) for r in rows]


@router.post("/{employee_id}/bonuses", response_model=BonusOut, status_code=201, summary="Award a bonus")
async def award_bonus(
    employee_id: uuid.UUID,
    payload: BonusCreateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.BONUS_MANAGE)),
    db=Depends(get_db),
) -> BonusOut:
    bonus = await EmployeeService(db).award_bonus(employee_id, payload, actor_id=current_user.id)
    return BonusOut.model_validate(bonus)


# ---------------------------------------------------------------------------
# Profile photo / certificate PDF — self-service ("me") and admin ("{id}")
# ---------------------------------------------------------------------------
async def _resolve_own_employee(db, current_user: CurrentUser) -> Employee:
    employee = await EmployeeRepository(db).get_by_user_id(current_user.id)
    if employee is None:
        raise AuthorizationError("This account is not linked to an employee record.")
    return employee


async def _upload_photo(db, employee: Employee, file: UploadFile) -> EmployeeDetail:
    content = await file.read()
    try:
        storage_key, safe_name = save_uploaded_file(
            category="employee-photos", entity_id=employee.id, content=content,
            original_filename=file.filename or "photo.jpg", content_type=file.content_type or "",
            allowed_content_types=ALLOWED_PHOTO_CONTENT_TYPES, max_bytes=MAX_PHOTO_BYTES,
        )
    except (UnsupportedFileTypeError, FileTooLargeError) as exc:
        raise ValidationAppError(str(exc)) from exc

    delete_file(employee.photo_storage_key)
    employee.photo_storage_key = storage_key
    employee.photo_original_name = safe_name
    return await _to_detail(db, employee)


async def _upload_certificate(db, employee: Employee, file: UploadFile) -> EmployeeDetail:
    content = await file.read()
    try:
        storage_key, safe_name = save_uploaded_file(
            category="employee-certificates", entity_id=employee.id, content=content,
            original_filename=file.filename or "certificates.pdf", content_type=file.content_type or "",
            allowed_content_types=ALLOWED_PDF_CONTENT_TYPES, max_bytes=MAX_CERTIFICATE_PDF_BYTES,
        )
    except (UnsupportedFileTypeError, FileTooLargeError) as exc:
        raise ValidationAppError(str(exc)) from exc

    delete_file(employee.certificate_storage_key)
    employee.certificate_storage_key = storage_key
    employee.certificate_original_name = safe_name
    return await _to_detail(db, employee)


def _serve_file(storage_key: str | None, original_name: str | None, media_type: str) -> Response:
    path = get_file_path(storage_key)
    if path is None:
        raise ResourceNotFoundError("No file uploaded.")
    filename = original_name or path.name
    headers = {"Content-Disposition": f'inline; filename="{filename}"', **SAFE_SERVE_HEADERS}
    return Response(content=path.read_bytes(), media_type=media_type, headers=headers)


@router.post("/me/photo", status_code=202, summary="Send a new photo of myself for approval (it goes live only when the office approves)")
async def upload_my_photo(file: UploadFile, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    """A person's own photo is never published directly: it must pass the automatic face check and then wait for an
    administrator (app/selfservice). The administration's own upload, /{id}/photo, still publishes at once."""
    from app.selfservice.service import ProfileChangeService, request_out, subject_for_user

    subj = await subject_for_user(db, current_user.id)
    req = await ProfileChangeService(db).ask_photo(subj, current_user.id, await file.read(), file.filename or "photo.jpg", file.content_type or "")
    await db.commit()
    return {"pending_approval": True, "request": await request_out(db, req)}


@router.get("/me/photo", summary="Download my own profile photo")
async def get_my_photo(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    employee = await _resolve_own_employee(db, current_user)
    return _serve_file(employee.photo_storage_key, employee.photo_original_name, "image/jpeg")


@router.delete("/me/photo", status_code=204, summary="Remove my own profile photo")
async def delete_my_photo(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> None:
    employee = await _resolve_own_employee(db, current_user)
    delete_file(employee.photo_storage_key)
    employee.photo_storage_key = None
    employee.photo_original_name = None


@router.post("/me/certificate", response_model=EmployeeDetail, summary="Upload my own combined certificate PDF")
async def upload_my_certificate(file: UploadFile, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> EmployeeDetail:
    employee = await _resolve_own_employee(db, current_user)
    return await _upload_certificate(db, employee, file)


@router.get("/me/certificate", summary="Download my own combined certificate PDF")
async def get_my_certificate(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    employee = await _resolve_own_employee(db, current_user)
    return _serve_file(employee.certificate_storage_key, employee.certificate_original_name, "application/pdf")


@router.delete("/me/certificate", status_code=204, summary="Remove my own combined certificate PDF")
async def delete_my_certificate(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> None:
    employee = await _resolve_own_employee(db, current_user)
    delete_file(employee.certificate_storage_key)
    employee.certificate_storage_key = None
    employee.certificate_original_name = None


@router.post("/{employee_id}/photo", response_model=EmployeeDetail, summary="Upload an employee's profile photo (admin)")
async def admin_upload_photo(
    employee_id: uuid.UUID, file: UploadFile,
    current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_UPDATE)), db=Depends(get_db),
) -> EmployeeDetail:
    employee = await EmployeeService(db).get_employee(employee_id)
    return await _upload_photo(db, employee, file)


@router.get("/{employee_id}/photo", summary="Download an employee's profile photo (staff)")
async def admin_get_photo(
    employee_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_VIEW)), db=Depends(get_db),
) -> Response:
    employee = await EmployeeService(db).get_employee(employee_id)
    return _serve_file(employee.photo_storage_key, employee.photo_original_name, "image/jpeg")


@router.delete("/{employee_id}/photo", status_code=204, summary="Remove an employee's profile photo (admin)")
async def admin_delete_photo(
    employee_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_UPDATE)), db=Depends(get_db),
) -> None:
    employee = await EmployeeService(db).get_employee(employee_id)
    delete_file(employee.photo_storage_key)
    employee.photo_storage_key = None
    employee.photo_original_name = None


@router.post("/{employee_id}/certificate", response_model=EmployeeDetail, summary="Upload an employee's combined certificate PDF (admin)")
async def admin_upload_certificate(
    employee_id: uuid.UUID, file: UploadFile,
    current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_UPDATE)), db=Depends(get_db),
) -> EmployeeDetail:
    employee = await EmployeeService(db).get_employee(employee_id)
    return await _upload_certificate(db, employee, file)


@router.get("/{employee_id}/certificate", summary="Download an employee's combined certificate PDF (staff)")
async def admin_get_certificate(
    employee_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_VIEW)), db=Depends(get_db),
) -> Response:
    employee = await EmployeeService(db).get_employee(employee_id)
    return _serve_file(employee.certificate_storage_key, employee.certificate_original_name, "application/pdf")


@router.delete("/{employee_id}/certificate", status_code=204, summary="Remove an employee's combined certificate PDF (admin)")
async def admin_delete_certificate(
    employee_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_UPDATE)), db=Depends(get_db),
) -> None:
    employee = await EmployeeService(db).get_employee(employee_id)
    delete_file(employee.certificate_storage_key)
    employee.certificate_storage_key = None
    employee.certificate_original_name = None


# ---------------------------------------------------------------------------
# Full profile — the "everything about this employee in one place" view.
# Counterpart to app/students/router.py's equivalent endpoint. Built at
# the router layer, not inside EmployeeService, for the same reason:
# EmployeeService has no dependency on Academics/Fees/Library/Fines/
# Complaints, and this aggregation is the one place that legitimately
# needs all of them.
# ---------------------------------------------------------------------------
@router.get(
    "/{employee_id}/full-profile", response_model=EmployeeFullProfileResponse,
    summary="Everything about this employee in one place — personal, payroll, teaching, fees, library, fines, complaints",
)
async def get_employee_full_profile(
    employee_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.EMPLOYEE_VIEW)),
    db=Depends(get_db),
) -> EmployeeFullProfileResponse:
    employee = await EmployeeService(db).get_employee(employee_id)

    job_role_title = None
    if employee.job_role_id is not None:
        job_role = await db.get(JobRole, employee.job_role_id)
        job_role_title = job_role.title if job_role else None

    # Qualifications
    from app.profiles.models import ProfilePartyType
    from app.profiles.schemas import QualificationOut as _QualOut
    from app.profiles.service import QualificationService

    quals = await QualificationService(db).list_for_party(ProfilePartyType.EMPLOYEE, employee_id)
    qualifications = [_QualOut.model_validate(q) for q in quals]

    # Compensation (salary history + bonuses)
    payroll_repo = EmployeeRepository(db)
    current_salary_row = await payroll_repo.get_current_salary(employee_id)
    current_salary = float(current_salary_row.base_salary) if current_salary_row else None
    salary_rows = await payroll_repo.get_salary_history(employee_id)
    salary_history = [
        SalaryHistoryEntry(
            base_salary=float(r.base_salary), effective_from=r.effective_from, effective_to=r.effective_to,
            reason=r.reason,
        )
        for r in salary_rows
    ]
    bonus_rows = await payroll_repo.get_bonuses(employee_id)
    bonuses = [BonusEntry(amount=float(b.amount), reason=b.reason, awarded_at=b.awarded_at) for b in bonus_rows]

    # Teaching summary — only populated if they hold the TEACHER role,
    # same role-based check (not job-title-based) as the "assign
    # teacher" search filter.
    teaching = None
    if employee.user_id is not None:
        from app.auth.models import Role, UserRole

        has_teacher_role = (
            await db.execute(
                select(UserRole)
                .join(Role, UserRole.role_id == Role.id)
                .where(UserRole.user_id == employee.user_id, Role.code == Roles.TEACHER)
            )
        ).scalar_one_or_none() is not None

        if has_teacher_role:
            from app.academics.models import EnrollmentOutcome
            from app.academics.repository import CourseOfferingRepository, EnrollmentRepository

            offering_repo = CourseOfferingRepository(db)
            enrollment_repo = EnrollmentRepository(db)
            offerings = await offering_repo.list_by_teacher(employee_id)

            total_students = 0
            passed_count = 0
            failed_count = 0
            for offering in offerings:
                enrollments = await enrollment_repo.list_for_offering(offering.id)
                total_students += len(enrollments)
                for e in enrollments:
                    if e.outcome == EnrollmentOutcome.PASSED:
                        passed_count += 1
                    elif e.outcome == EnrollmentOutcome.FAILED:
                        failed_count += 1

            graded = passed_count + failed_count
            teaching = TeachingSummary(
                offerings_count=len(offerings), total_students_taught=total_students,
                passed_count=passed_count, failed_count=failed_count,
                pass_rate=(passed_count / graded) if graded > 0 else None,
            )

    # Fee summary (as an employee payer): the same figures as their own fee account.
    from app.fees.account_service import AccountService
    from app.fees.models import PayerType

    fee_acct = await AccountService(db).build(PayerType.EMPLOYEE, employee_id, staff_view=True)
    fs = fee_acct["summary"]
    fee_summary = EmployeeFeeSummary(
        total_assignments=sum(1 for a in fee_acct["assignments"] if a["status"] == "ACTIVE"),
        total_due=round(fs["charged"] - fs["concession"], 2), total_paid=fs["paid"], total_outstanding=fs["remaining"],
    )

    # Library summary (as an employee borrower)

    from app.library.models import BorrowerType, IssueStatus
    from app.library.repository import BookIssueRepository

    library_repo = BookIssueRepository(db)
    active_loans = await library_repo.list_active_for_borrower(BorrowerType.EMPLOYEE, employee_id)
    history_loans = await library_repo.list_history_for_borrower(BorrowerType.EMPLOYEE, employee_id)
    overdue_loans = [loan for loan in active_loans if loan.status == IssueStatus.ISSUED and loan.due_date < institute_today()]
    library_summary = EmployeeLibrarySummary(
        active_loans=len(active_loans), overdue_loans=len(overdue_loans), past_loans=len(history_loans)
    )

    # Fine summary
    from app.fines.models import FinePayerType
    from app.fines.repository import FineRepository

    fine_balance = await FineRepository(db).balance_for(FinePayerType.EMPLOYEE, employee_id)
    fine_summary = EmployeeFineSummary(
        total_pending=fine_balance["total_pending"], total_paid=fine_balance["total_paid"],
        total_waived=fine_balance["total_waived"],
    )

    # Complaint summary
    from app.complaints.models import ComplaintPartyType
    from app.complaints.repository import ComplaintRepository

    complaint_repo = ComplaintRepository(db)
    filed_by = await complaint_repo.list_for_complainant(ComplaintPartyType.EMPLOYEE, employee_id)
    filed_against = await complaint_repo.list_all(target_type=ComplaintPartyType.EMPLOYEE, target_id=employee_id)
    complaint_summary = EmployeeComplaintSummary(filed_by_them=len(filed_by), filed_against_them=len(filed_against))

    return EmployeeFullProfileResponse(
        id=employee.id, first_name=employee.first_name, last_name=employee.last_name,
        employee_code=employee.employee_code, cnic=employee.cnic, contact_no=employee.contact_no,
        date_of_birth=employee.date_of_birth, gender=employee.gender, hire_date=employee.hire_date,
        department=employee.department, status=employee.status, job_role_title=job_role_title,
        religion=employee.religion, email=employee.email, blood_group=employee.blood_group,
        marital_status=employee.marital_status, employment_type=employee.employment_type,
        emergency_contact_name=employee.emergency_contact_name, emergency_contact_no=employee.emergency_contact_no,
        photo_original_name=employee.photo_original_name, has_photo=employee.has_photo,
        certificate_original_name=employee.certificate_original_name, has_certificate=employee.has_certificate,
        present_address=employee.address_line, province=employee.province, district=employee.district,
        city=employee.city,
        qualifications=qualifications,
        current_salary=current_salary, salary_history=salary_history, bonuses=bonuses,
        teaching=teaching,
        fees=fee_summary, library=library_summary, fines=fine_summary, complaints=complaint_summary,
    )


# ============================================================ extra access roles
class AccessRolesIn(BaseModel):
    """The EXTRA roles this person should have (the job title's own role is kept automatically)."""

    extra_roles: list[str] = Field(default_factory=list, max_length=10)


@router.get("/{employee_id}/access-roles", summary="The roles this staff member's login has: main (from job title) and extra (administrator)")
async def get_access_roles(employee_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.ROLE_MANAGE)),
                           db=Depends(get_db)) -> dict:
    from app.employees.access_roles import AccessRoleService

    return await AccessRoleService(db).get(employee_id)


@router.put("/{employee_id}/access-roles", summary="Set the extra roles (e.g. a teacher who is also Exam Controller; a second administrator). Signs the person out.")
async def set_access_roles(employee_id: uuid.UUID, payload: AccessRolesIn,
                           current_user: CurrentUser = Depends(require_permission(Permissions.ROLE_MANAGE)), db=Depends(get_db)) -> dict:
    from app.employees.access_roles import AccessRoleService

    return await AccessRoleService(db).set_extra(employee_id, payload.extra_roles, actor_user_id=current_user.id)