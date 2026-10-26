from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_permission
from app.core.exceptions import ResourceNotFoundError
from app.core.storage import get_file_path
from app.selfservice.models import ProfileChangeRequest
from app.selfservice.service import FIELDS, ProfileChangeService, request_out, subject_for_user, subject_of

router = APIRouter(prefix="/profile-changes", tags=["profile changes (self-service)"])
decide = require_permission(Permissions.PROFILE_CHANGE_DECIDE)
SAFE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


class AskIn(BaseModel):
    values: dict[str, str | None] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=500)


class DecideIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


def _image(key: str | None) -> Response:
    path = get_file_path(key)
    if path is None:
        raise ResourceNotFoundError("No photo.")
    kind = "image/png" if path.suffix.lower() == ".png" else "image/webp" if path.suffix.lower() == ".webp" else "image/jpeg"
    return Response(content=path.read_bytes(), media_type=kind, headers=SAFE)


# ------------------------------------------------------------------ the person
@router.get("/me", summary="My editable details, what is waiting for approval, and my earlier requests")
async def mine(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    subj = await subject_for_user(db, current_user.id)
    svc = ProfileChangeService(db)
    pending = await svc.pending_for(subj)
    history = (await db.execute(select(ProfileChangeRequest).where(
        ProfileChangeRequest.subject_type == subj.kind, ProfileChangeRequest.subject_id == subj.row.id, ProfileChangeRequest.status != "PENDING")
        .order_by(ProfileChangeRequest.created_at.desc()).limit(10))).scalars().all()
    return {"kind": subj.kind, "values": await subj.values(),
            "fields": [{"key": f.key, "label": f.label, "kind": f.kind, "max_len": f.max_len, "required": f.required} for f in FIELDS[subj.kind]],
            "pending": await request_out(db, pending) if pending else None,
            "history": [await request_out(db, r) for r in history]}


@router.post("/me", summary="Ask to change my contact details (the office approves before anything changes)")
async def ask(body: AskIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    subj = await subject_for_user(db, current_user.id)
    req = await ProfileChangeService(db).ask_fields(subj, current_user.id, body.values, body.note)
    await db.commit()
    return await request_out(db, req)


@router.post("/me/photo", summary="Send a new photo for approval (it must show one clear, uncovered face)")
async def ask_photo(file: UploadFile = File(...), current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    subj = await subject_for_user(db, current_user.id)
    content = await file.read()
    req = await ProfileChangeService(db).ask_photo(subj, current_user.id, content, file.filename or "photo.jpg", file.content_type or "")
    await db.commit()
    return await request_out(db, req)


@router.delete("/me/photo", summary="Take back the photo I sent (before it is decided)")
async def drop_photo(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    subj = await subject_for_user(db, current_user.id)
    req = await ProfileChangeService(db).drop_photo(subj, current_user.id)
    await db.commit()
    return {"pending": await request_out(db, req) if req and req.status == "PENDING" else None}


@router.get("/me/photo", summary="The photo I sent, while it waits")
async def my_pending_photo(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    subj = await subject_for_user(db, current_user.id)
    req = await ProfileChangeService(db).pending_for(subj)
    return _image(req.photo_storage_key if req else None)


@router.post("/me/{request_id}/withdraw", summary="Withdraw my request that is still waiting")
async def withdraw(request_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    req = await ProfileChangeService(db).cancel(request_id, current_user.id)
    await db.commit()
    return await request_out(db, req)


# ------------------------------------------------------------------ the administration
@router.get("", summary="Profile change requests (administration)")
async def list_requests(status: str = Query(default="PENDING", pattern="^(PENDING|APPROVED|REJECTED|CANCELLED|ALL)$"), q: str | None = None,
                        current_user: CurrentUser = Depends(decide), db=Depends(get_db)) -> list[dict]:
    stmt = select(ProfileChangeRequest).order_by(ProfileChangeRequest.created_at.asc() if status == "PENDING" else ProfileChangeRequest.updated_at.desc()).limit(300)
    if status != "ALL":
        stmt = stmt.where(ProfileChangeRequest.status == status)
    out = []
    for r in (await db.execute(stmt)).scalars().all():
        try:
            row = await request_out(db, r, with_subject=True)
        except ResourceNotFoundError:
            continue
        if q and q.strip().lower() not in (row["subject"]["name"] + " " + row["subject"]["code"]).lower():
            continue
        out.append(row)
    return out


@router.get("/count", summary="How many profile changes are waiting (for the dashboard)")
async def count(current_user: CurrentUser = Depends(decide), db=Depends(get_db)) -> dict:
    return {"waiting": await ProfileChangeService(db).count_pending()}


@router.get("/{request_id}/photo", summary="The new photo in a request (administration)")
async def request_photo(request_id: uuid.UUID, current_user: CurrentUser = Depends(decide), db=Depends(get_db)) -> Response:
    req = await db.get(ProfileChangeRequest, request_id)
    return _image(req.photo_storage_key if req else None)


@router.get("/{request_id}/current-photo", summary="The photo on record now, to compare (administration)")
async def current_photo(request_id: uuid.UUID, current_user: CurrentUser = Depends(decide), db=Depends(get_db)) -> Response:
    req = await db.get(ProfileChangeRequest, request_id)
    if req is None:
        raise ResourceNotFoundError("Request not found.")
    subj = await subject_of(db, req)
    return _image(subj.row.photo_storage_key)


@router.post("/{request_id}/approve", summary="Approve: every change in the request goes live (administration)")
async def approve(request_id: uuid.UUID, current_user: CurrentUser = Depends(decide), db=Depends(get_db)) -> dict:
    req = await ProfileChangeService(db).decide(request_id, approve=True, actor_id=current_user.id, reason=None)
    await db.commit()
    return await request_out(db, req, with_subject=True)


@router.post("/{request_id}/reject", summary="Reject with a reason the person will see (administration)")
async def reject(request_id: uuid.UUID, body: DecideIn, current_user: CurrentUser = Depends(decide), db=Depends(get_db)) -> dict:
    req = await ProfileChangeService(db).decide(request_id, approve=False, actor_id=current_user.id, reason=body.reason)
    await db.commit()
    return await request_out(db, req, with_subject=True)
