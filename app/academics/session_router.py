"""Academic sessions API (app/academics/sessions.py)."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.academics.sessions import SessionService
from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission

router = APIRouter(prefix="/academics/sessions", tags=["academic-sessions"])
manage = require_permission(Permissions.COURSE_OFFERING_MANAGE)


class SuggestionItem(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    term_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    batches: dict[str, dict] = Field(default_factory=dict)


class SaveIn(BaseModel):
    items: list[SuggestionItem] = Field(min_length=1, max_length=100)


class SessionIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    start_date: date | None = None
    end_date: date | None = None


class SessionPatch(BaseModel):
    name: str | None = Field(default=None, max_length=50)
    start_date: date | None = None
    end_date: date | None = None
    term_ids: list[uuid.UUID] | None = Field(default=None, max_length=300)


class NextIn(BaseModel):
    new_name: str = Field(min_length=1, max_length=50)
    labels: dict[str, str] = Field(default_factory=dict)          # term id -> the new term's name, when changed in the preview
    start_date: date | None = None
    end_date: date | None = None
    enroll: bool = True


@router.get("", summary="Sessions with their batch terms (current first), and terms in no session")
async def groups(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    # term names are not sensitive and many screens group their term lists with this (see academic-terms)
    return await SessionService(db).groups()


@router.get("/suggest", summary="Sessions proposed from the term names, and batches for terms without one (nothing is saved)")
async def suggest(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SessionService(db).suggest()


@router.post("/suggest/save", summary="Save the checked suggestions")
async def save(payload: SaveIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SessionService(db).save_suggestions([i.model_dump() for i in payload.items], actor_id=current_user.id)


@router.post("", status_code=201, summary="Add a session")
async def create(payload: SessionIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    s = await SessionService(db).create(name=payload.name, start_date=payload.start_date, end_date=payload.end_date, actor_id=current_user.id)
    return {"id": s.id, "name": s.name}


@router.patch("/{session_id}", summary="Rename a session, set its dates or its terms")
async def update(session_id: uuid.UUID, payload: SessionPatch, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    s = await SessionService(db).update(session_id, name=payload.name, start_date=payload.start_date, end_date=payload.end_date,
                                        term_ids=payload.term_ids, actor_id=current_user.id)
    return {"id": s.id, "name": s.name}


@router.delete("/{session_id}", summary="Remove a session (only the grouping; its terms stay)")
async def delete(session_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    await SessionService(db).delete(session_id, actor_id=current_user.id)
    return {"ok": True}


@router.post("/{session_id}/next-preview", summary="What starting the next session would create (nothing is saved)")
async def next_preview(session_id: uuid.UUID, payload: NextIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SessionService(db).next_preview(session_id, new_name=payload.new_name, labels=payload.labels)


@router.post("/{session_id}/start-next", summary="Start the next session: next batch terms, their sections, promoted students enrolled (all or nothing)")
async def start_next(session_id: uuid.UUID, payload: NextIn,
                     current_user: CurrentUser = Depends(require_permission(Permissions.ENROLLMENT_MANAGE)),
                     _m: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SessionService(db).start_next(session_id, new_name=payload.new_name, start_date=payload.start_date, end_date=payload.end_date,
                                               labels=payload.labels, enroll=payload.enroll, actor_id=current_user.id)
