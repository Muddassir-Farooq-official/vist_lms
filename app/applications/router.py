from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response

from app.applications.models import ApplicantKind, ApplicationStatus
from app.applications.schemas import (
    ApplicationOut,
    BadgeOut,
    CommentIn,
    OverviewOut,
    ReviewIn,
    TypeIn,
    TypeOut,
    TypeUpdate,
)
from app.applications.service import ApplicationService
from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission
from app.core.storage import MAX_ATTACHMENT_BYTES, SAFE_SERVE_HEADERS, attachment_is_inline, get_file_path
from app.core.exceptions import ResourceNotFoundError

router = APIRouter(prefix="/applications", tags=["applications"])
submit = require_permission(Permissions.APPLICATION_SUBMIT)
manage = require_permission(Permissions.APPLICATION_MANAGE)


def _is_admin(user: CurrentUser) -> bool:
    return Permissions.APPLICATION_MANAGE in user.permissions


async def _read(files: list[UploadFile]) -> list[tuple[str, bytes, str | None]]:
    out = []
    for f in files:
        if not (f.filename or "").strip():
            continue                                  # browsers send an empty part when no file was chosen
        out.append((f.filename, await f.read(MAX_ATTACHMENT_BYTES + 1), f.content_type))
    return out


def _type_out(t, usage=None) -> TypeOut:
    return TypeOut(id=t.id, name=t.name, description=t.description, for_students=t.for_students, for_employees=t.for_employees, requires_dates=t.requires_dates,
                   requires_attachment=t.requires_attachment, yearly_limit_days=t.yearly_limit_days, is_active=t.is_active, sort_order=t.sort_order, usage=usage)


# ============================================================ kinds of application (admin manages, applicants choose)
@router.get("/types", response_model=list[TypeOut], summary="What I can apply for, with my leave used so far this year (applicant)")
async def my_types(current_user: CurrentUser = Depends(submit), db=Depends(get_db)) -> list[TypeOut]:
    service = ApplicationService(db)
    who = await service.applicant(current_user.id)
    return [_type_out(t, await service.usage(current_user.id, t)) for t in await service.list_types(kind=who["kind"])]


@router.get("/types/all", response_model=list[TypeOut], summary="Every kind of application, including switched-off ones (admin)")
async def all_types(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[TypeOut]:
    return [_type_out(t) for t in await ApplicationService(db).list_types(active_only=False)]


@router.post("/types", response_model=TypeOut, status_code=201, summary="Add a kind of application (admin)")
async def create_type(payload: TypeIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> TypeOut:
    return _type_out(await ApplicationService(db).create_type(data=payload.model_dump(), actor_id=current_user.id))


@router.patch("/types/{type_id}", response_model=TypeOut, summary="Change or switch off a kind of application (admin)")
async def update_type(type_id: uuid.UUID, payload: TypeUpdate, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> TypeOut:
    return _type_out(await ApplicationService(db).update_type(type_id, changes=payload.model_dump(exclude_unset=True), actor_id=current_user.id))


# ============================================================ applying (students and employees)
@router.post("", response_model=ApplicationOut, status_code=201, summary="Submit an application, with any documents (multipart form)")
async def create_application(
    type_id: uuid.UUID = Form(...), subject: str = Form(..., max_length=200), details: str = Form(..., max_length=5000),
    from_date: date | None = Form(default=None), to_date: date | None = Form(default=None), files: list[UploadFile] = File(default=[]),
    current_user: CurrentUser = Depends(submit), db=Depends(get_db),
) -> ApplicationOut:
    service = ApplicationService(db)
    app = await service.create(user_id=current_user.id, type_id=type_id, subject=subject, details=details, from_date=from_date, to_date=to_date, files=await _read(files))
    return ApplicationOut(**await service.detail(app.id, user_id=current_user.id, is_admin=False))


@router.get("/mine", response_model=list[ApplicationOut], summary="My applications, newest first (applicant)")
async def my_applications(current_user: CurrentUser = Depends(submit), db=Depends(get_db)) -> list[ApplicationOut]:
    return [ApplicationOut(**r) for r in await ApplicationService(db).mine(current_user.id)]


@router.get("/badge", response_model=BadgeOut, summary="A number for the menu: waiting for the administration (admin), or waiting for my answer (applicant)")
async def badge(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> BadgeOut:
    return BadgeOut(count=await ApplicationService(db).badge(current_user.id, _is_admin(current_user)))


# ============================================================ the administration
@router.get("/overview", response_model=OverviewOut, summary="Totals and charts for the administration")
async def overview(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> OverviewOut:
    return OverviewOut(**await ApplicationService(db).overview())


@router.get("", response_model=list[ApplicationOut], summary="All applications with filters (admin)")
async def list_applications(
    status: ApplicationStatus | None = None, needs_action: bool = False, type_id: uuid.UUID | None = None, kind: ApplicantKind | None = None,
    q: str | None = None, limit: int = 50, offset: int = 0, current_user: CurrentUser = Depends(manage), db=Depends(get_db),
) -> list[ApplicationOut]:
    rows = await ApplicationService(db).list_for_admin(status=status, needs_action=needs_action, type_id=type_id, kind=kind, q=q, limit=limit, offset=offset)
    return [ApplicationOut(**r) for r in rows]


@router.get("/attachments/{attachment_id}/file", summary="Open or download an attached document (the applicant and the administration only)")
async def download(attachment_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    att = await ApplicationService(db).get_attachment(attachment_id, user_id=current_user.id, is_admin=_is_admin(current_user))
    path = get_file_path(att.storage_key)
    if path is None:
        raise ResourceNotFoundError("The file is no longer available.")
    return Response(content=path.read_bytes(), media_type=att.content_type,
                    headers={"Content-Disposition": f'{"inline" if attachment_is_inline(att.content_type) else "attachment"}; filename="{att.original_name}"', **SAFE_SERVE_HEADERS})


@router.get("/{application_id}", response_model=ApplicationOut, summary="One application in full, with its conversation (the applicant or the administration)")
async def get_application(application_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> ApplicationOut:
    return ApplicationOut(**await ApplicationService(db).detail(application_id, user_id=current_user.id, is_admin=_is_admin(current_user)))


@router.post("/{application_id}/comments", response_model=ApplicationOut, summary="Add a message to the conversation")
async def add_comment(application_id: uuid.UUID, payload: CommentIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> ApplicationOut:
    service = ApplicationService(db)
    admin = _is_admin(current_user)
    await service.comment(application_id, user_id=current_user.id, is_admin=admin, body=payload.body)
    return ApplicationOut(**await service.detail(application_id, user_id=current_user.id, is_admin=admin))


@router.post("/{application_id}/attachments", response_model=ApplicationOut, summary="Add more documents while it is still open (applicant)")
async def add_files(application_id: uuid.UUID, files: list[UploadFile] = File(default=[]), current_user: CurrentUser = Depends(submit), db=Depends(get_db)) -> ApplicationOut:
    service = ApplicationService(db)
    await service.add_attachments(application_id, user_id=current_user.id, files=await _read(files))
    return ApplicationOut(**await service.detail(application_id, user_id=current_user.id, is_admin=False))


@router.post("/{application_id}/withdraw", response_model=ApplicationOut, summary="Take the application back (applicant, while it is still open)")
async def withdraw(application_id: uuid.UUID, current_user: CurrentUser = Depends(submit), db=Depends(get_db)) -> ApplicationOut:
    service = ApplicationService(db)
    await service.withdraw(application_id, user_id=current_user.id)
    return ApplicationOut(**await service.detail(application_id, user_id=current_user.id, is_admin=False))


@router.post("/{application_id}/review", response_model=ApplicationOut, summary="Start reviewing, ask for more information, approve or reject (admin)")
async def review(application_id: uuid.UUID, payload: ReviewIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> ApplicationOut:
    service = ApplicationService(db)
    await service.review(application_id, admin_id=current_user.id, action=payload.action.value, note=payload.note)
    return ApplicationOut(**await service.detail(application_id, user_id=current_user.id, is_admin=True))