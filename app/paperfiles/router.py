"""Exam paper files API (app/paperfiles/service.py). Teachers send; the exam office prints or returns. No student access."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_any_permission, require_permission
from app.core.exceptions import ResourceNotFoundError

router = APIRouter(prefix="/exam-paper-files", tags=["exam paper files"])
sender = require_any_permission(Permissions.MARKS_ENTER, Permissions.EXAM_SCHEDULE_MANAGE)      # teachers and the exam office
office = require_permission(Permissions.EXAM_SCHEDULE_MANAGE)
MAX_READ = 16 * 1024 * 1024


def _svc(db):
    from app.paperfiles.service import PaperFileService

    return PaperFileService(db)


def _office(user: CurrentUser) -> bool:
    return Permissions.EXAM_SCHEDULE_MANAGE in user.permissions


@router.get("/mine", summary="My coming exam papers and whether each file is sent, printed or returned")
async def mine(current_user: CurrentUser = Depends(sender), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).mine(current_user)


@router.post("/papers/{paper_id}", status_code=201, summary="Send the paper file (PDF or Word); a new file replaces the one not yet printed")
async def send(paper_id: uuid.UUID, file: UploadFile = File(...), note: str | None = Form(default=None, max_length=500),
               current_user: CurrentUser = Depends(sender), db=Depends(get_db)) -> dict:
    content = await file.read(MAX_READ + 1)
    return await _svc(db).send(paper_id, content=content, file_name=file.filename or "paper", content_type=file.content_type, note=note,
                               user=current_user, office=_office(current_user))


@router.get("/papers/{paper_id}/history", summary="Every file sent for this paper")
async def history(paper_id: uuid.UUID, current_user: CurrentUser = Depends(sender), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).history(paper_id, current_user, office=_office(current_user))


@router.get("/files/{file_id}", summary="Download a paper file (the course's teacher or the exam office)")
async def download(file_id: uuid.UUID, current_user: CurrentUser = Depends(sender), db=Depends(get_db)):
    from app.core.storage import get_file_path

    f = await _svc(db).file_for_download(file_id, current_user, office=_office(current_user))
    path = get_file_path(f.storage_key)
    if path is None:
        raise ResourceNotFoundError("The file is missing on the server.")
    await db.commit()                      # keep the download in the audit log
    return FileResponse(path, media_type=f.content_type, filename=f.file_name, headers={"Cache-Control": "no-store"})


@router.get("/schedules", summary="Datesheets, newest first (exam office)")
async def schedules(current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).schedules()


@router.get("/schedules/{schedule_id}", summary="Every paper of a datesheet with its file status (exam office)")
async def overview(schedule_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).overview(schedule_id)


class DecideIn(BaseModel):
    printed: bool
    note: str | None = Field(default=None, max_length=500)


@router.post("/files/{file_id}/decide", summary="Mark printed, or return to the teacher with what to correct")
async def decide(file_id: uuid.UUID, payload: DecideIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).decide(file_id, printed=payload.printed, note=payload.note, actor_id=current_user.id)
