from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import Response

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.core.exceptions import ResourceNotFoundError
from app.core.pagination import Page, page_params, PageParams
from app.core.queue import get_imports_queue
from app.imports.repository import ImportRepository
from app.imports.schemas import ImportJobCreatedResponse, ImportJobOut, ImportRowErrorOut
from app.imports.service import ImportService
from app.imports.tasks import run_import_job
from app.imports.validators import generate_template_csv, generate_template_xlsx

router = APIRouter(prefix="/imports/students", tags=["imports"])


@router.get("/template", summary="Download the student CSV import template (plain .csv)")
async def download_template(
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_IMPORT)),
) -> Response:
    csv_text = generate_template_csv()
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="vist_lms_student_import_template.csv"'},
    )


@router.get(
    "/template.xlsx",
    summary="Download the student import template as .xlsx (recommended — phone/CNIC columns pre-formatted as Text)",
)
async def download_template_xlsx(
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_IMPORT)),
) -> Response:
    """The .csv template can only protect its own example row from Excel's
    leading-zero-stripping behavior — CSV carries no cell-formatting
    metadata, so any NEW row the admin types is still at Excel's mercy.
    This .xlsx version pre-sets the phone/CNIC columns to Text format for
    1000 rows before any data is entered, which is what actually prevents
    the corruption for real admin-entered data, not just the demo row.
    The admin still needs to 'Save As CSV' before uploading — the import
    endpoint only accepts .csv — but because the columns were already
    Text, that save preserves the literal digits including leading zeros."""
    xlsx_bytes = generate_template_xlsx()
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="vist_lms_student_import_template.xlsx"'},
    )


@router.post("", response_model=ImportJobCreatedResponse, status_code=202, summary="Upload a student CSV for import")
async def upload_students_csv(
    file: UploadFile,
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_IMPORT)),
    db=Depends(get_db),
) -> ImportJobCreatedResponse:
    """Accepts the file, records an import_job, and hands the actual
    work to a real background worker process via RQ (Redis-backed) —
    processed by the separate `worker` service (see docker-compose.yml
    and app/imports/tasks.py), not FastAPI's own request/response
    lifecycle. Poll GET /imports/students/{id} for status."""
    file_bytes = await file.read()
    service = ImportService(db)
    job = await service.create_job_from_upload(
        file_bytes=file_bytes, filename=file.filename or "upload.csv", uploaded_by=current_user.id
    )
    job_id = job.id

    # Commit explicitly before enqueueing — the worker process runs in
    # its own connection/process entirely and needs the job row to
    # already be durably visible the moment it picks up the job, which
    # could happen within milliseconds of the enqueue call.
    await db.commit()

    get_imports_queue().enqueue(run_import_job, str(job_id), file_bytes)

    return ImportJobCreatedResponse(
        import_job_id=job_id, status=job.status, message="Import accepted and is processing. Poll for status."
    )


@router.get("/{import_id}", response_model=ImportJobOut, summary="Get import job status")
async def get_import_job(
    import_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_IMPORT)),
    db=Depends(get_db),
) -> ImportJobOut:
    job = await ImportRepository(db).get_by_id(import_id)
    if job is None:
        raise ResourceNotFoundError("Import job not found.")
    return ImportJobOut.model_validate(job)


@router.get("/{import_id}/errors", response_model=Page[ImportRowErrorOut], summary="Get import row errors")
async def get_import_errors(
    import_id: uuid.UUID,
    page: PageParams = Depends(page_params),
    current_user: CurrentUser = Depends(require_permission(Permissions.STUDENT_IMPORT)),
    db=Depends(get_db),
) -> Page[ImportRowErrorOut]:
    job = await ImportRepository(db).get_by_id(import_id)
    if job is None:
        raise ResourceNotFoundError("Import job not found.")

    errors, total = await ImportRepository(db).list_row_errors(import_id, offset=page.offset, limit=page.page_size)
    return Page[ImportRowErrorOut](
        items=[ImportRowErrorOut.model_validate(e) for e in errors],
        total=total,
        page=page.page,
        page_size=page.page_size,
    )