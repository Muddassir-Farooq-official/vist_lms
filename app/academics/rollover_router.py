"""Semester rollover wizard API (app/academics/rollover.py). Administrators only."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions, Roles
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_any_role, require_permission

router = APIRouter(prefix="/rollover", tags=["rollover"])
admin = require_permission(Permissions.SYSTEM_HEALTH)


def _svc(db):
    from app.academics.rollover import RolloverService

    return RolloverService(db)


@router.get("/{session_id}", summary="Where the session's rollover stands, and its promotions")
async def overview(session_id: uuid.UUID, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await _svc(db).overview(session_id)


class CloseIn(BaseModel):
    term_ids: list[uuid.UUID] = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=300)


@router.get("/{session_id}/close", summary="Step 1 preview: each batch term and what is unfinished")
async def close_preview(session_id: uuid.UUID, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await _svc(db).close_preview(session_id)


@router.post("/{session_id}/close", summary="Step 1: end the semester for the chosen terms and close them (all or nothing)")
async def close(session_id: uuid.UUID, payload: CloseIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await _svc(db).close(session_id, term_ids=payload.term_ids, reason=payload.reason, actor_id=current_user.id)


class Hold(BaseModel):
    student_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=200)


class PromoteIn(BaseModel):
    hold: list[Hold] = Field(default_factory=list)


@router.get("/{session_id}/promote", summary="Step 2 preview: who moves up, carry-over subjects, last-semester batches")
async def promote_preview(session_id: uuid.UUID, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await _svc(db).promote_preview(session_id)


@router.post("/{session_id}/promote", summary="Step 2: promote everyone except those held back (undo within 7 days)")
async def promote(session_id: uuid.UUID, payload: PromoteIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await _svc(db).promote(session_id, hold={h.student_id: h.reason for h in payload.hold}, actor_id=current_user.id)


@router.post("/runs/{run_id}/undo", summary="Undo a promotion (within 7 days; fees charged are kept)")
async def undo(run_id: uuid.UUID, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await _svc(db).undo(run_id, actor_id=current_user.id)


class NextIn(BaseModel):
    new_name: str = Field(min_length=2, max_length=50)
    start_date: date
    end_date: date
    labels: dict[str, str] | None = None
    enroll: bool = True
    same_teachers: bool = True


@router.get("/{session_id}/next", summary="Step 3 preview: the next session's batch terms, sections and enrollments")
async def next_preview(session_id: uuid.UUID, new_name: str = Query(..., min_length=2, max_length=50), current_user: CurrentUser = Depends(admin),
                       db=Depends(get_db)) -> dict:
    return await _svc(db).next_preview(session_id, new_name)


@router.post("/{session_id}/next", summary="Step 3: start the next session (terms, sections, enrollments, same teachers)")
async def start_next(session_id: uuid.UUID, payload: NextIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await _svc(db).start_next(session_id, new_name=payload.new_name, start_date=payload.start_date, end_date=payload.end_date,
                                     labels=payload.labels, enroll=payload.enroll, same_teachers=payload.same_teachers, actor_id=current_user.id)


class FeesIn(BaseModel):
    new_name: str = Field(min_length=2, max_length=50)
    percentage_change: float = Field(default=0, ge=-100, le=1000)
    effective_from: date


@router.get("/{session_id}/fees", summary="Step 4 preview: active fee structures to copy as drafts to the next batch terms")
async def fees_preview(session_id: uuid.UUID, new_name: str = Query(..., min_length=2, max_length=50), current_user: CurrentUser = Depends(admin),
                       db=Depends(get_db)) -> dict:
    return await _svc(db).fees_preview(session_id, new_name)


@router.post("/{session_id}/fees", summary="Step 4: copy them as DRAFT structures (never activated here)")
async def copy_fees(session_id: uuid.UUID, payload: FeesIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await _svc(db).copy_fees(session_id, new_name=payload.new_name, percentage_change=payload.percentage_change,
                                    effective_from=payload.effective_from, actor_id=current_user.id)
