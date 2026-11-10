"""Warnings to students and staff (app/discipline/service.py)."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission
from app.core.storage import SAFE_SERVE_HEADERS
from app.discipline.pdf import render_warning_letter
from app.discipline.service import WarningService

router = APIRouter(prefix="/discipline", tags=["warnings"])
issue = require_permission(Permissions.WARNING_ISSUE)
decide = require_permission(Permissions.WARNING_DECIDE)


class TypeIn(BaseModel):
    applies_to: str = Field(pattern="^(STUDENT|EMPLOYEE|BOTH)$")
    name: str = Field(min_length=3, max_length=120)


class TypeEditIn(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=120)
    is_active: bool | None = None


class WarningIn(BaseModel):
    kind: str = Field(pattern="^(STUDENT|EMPLOYEE)$")
    person_id: uuid.UUID
    violation_type_id: uuid.UUID
    note: str = Field(min_length=10, max_length=4000)
    incident_date: date | None = None


class NoteIn(BaseModel):
    note: str = Field(min_length=5, max_length=500)


class DecideIn(BaseModel):
    action: str = Field(pattern="^(NONE|SUSPEND|EXPEL|TERMINATE|FINE)$")
    note: str = Field(min_length=10, max_length=500)
    amount: float | None = Field(default=None, gt=0, le=1_000_000)          # action FINE


class ReplyIn(BaseModel):
    text: str = Field(min_length=10, max_length=4000)


def _pdf(parts: dict, pdf: bytes) -> Response:
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="warning-{parts["warning"]["reference"]}.pdf"', "Cache-Control": "no-store", **SAFE_SERVE_HEADERS})


@router.get("/violation-types", summary="The violations to choose from (kind: STUDENT or EMPLOYEE; all=1 includes those turned off)")
async def violation_types(kind: str | None = None, all: int = 0, current_user: CurrentUser = Depends(decide), db=Depends(get_db)) -> list[dict]:
    return await WarningService(db).types(kind.upper() if kind else None, include_off=bool(all))


@router.post("/violation-types", status_code=201, summary="Add a violation to the list (admin)")
async def add_violation_type(payload: TypeIn, current_user: CurrentUser = Depends(issue), db=Depends(get_db)) -> dict:
    t = await WarningService(db).save_type(actor_id=current_user.id, applies_to=payload.applies_to, name=payload.name)
    return {"id": t.id, "name": t.name}


@router.patch("/violation-types/{type_id}", summary="Rename a violation, or turn it off / on (admin)")
async def edit_violation_type(type_id: uuid.UUID, payload: TypeEditIn, current_user: CurrentUser = Depends(issue), db=Depends(get_db)) -> dict:
    t = await WarningService(db).save_type(actor_id=current_user.id, type_id=type_id, name=payload.name, is_active=payload.is_active)
    return {"id": t.id, "name": t.name, "is_active": t.is_active}


@router.get("/people", summary="Find a student or staff member to warn, with how many warnings count now")
async def people(kind: str, q: str, current_user: CurrentUser = Depends(issue), db=Depends(get_db)) -> list[dict]:
    return await WarningService(db).search(kind.upper(), q)


@router.get("/warnings", summary="Warnings (view: all | pending | replies; kind: STUDENT | EMPLOYEE)")
async def warnings(view: str = "all", kind: str | None = None, person_id: uuid.UUID | None = None, current_user: CurrentUser = Depends(decide),
                   db=Depends(get_db)) -> dict:
    return await WarningService(db).office(view=view, kind=kind.upper() if kind else None, person_id=person_id)


@router.post("/warnings", status_code=201, summary="Issue a warning (admin). The 3rd within 12 months is final.")
async def issue_warning(payload: WarningIn, current_user: CurrentUser = Depends(issue), db=Depends(get_db)) -> dict:
    w = await WarningService(db).issue(kind=payload.kind, person_id=payload.person_id, violation_type_id=payload.violation_type_id,
                                       note=payload.note, incident_date=payload.incident_date, actor_id=current_user.id)
    return {"id": w.id, "number": w.number, "is_final": w.is_final}


@router.post("/warnings/{warning_id}/withdraw", summary="Withdraw a warning: it stays on record but no longer counts")
async def withdraw_warning(warning_id: uuid.UUID, payload: NoteIn, current_user: CurrentUser = Depends(decide), db=Depends(get_db)) -> dict:
    w = await WarningService(db).withdraw(warning_id, actor_id=current_user.id, reason=payload.note)
    return {"id": w.id, "status": w.status}


@router.post("/warnings/{warning_id}/decide", summary="Decide the action on a final warning (applied to the person's record)")
async def decide_warning(warning_id: uuid.UUID, payload: DecideIn, current_user: CurrentUser = Depends(decide), db=Depends(get_db)) -> dict:
    w = await WarningService(db).decide(warning_id, actor_id=current_user.id, action=payload.action, note=payload.note, amount=payload.amount)
    return {"id": w.id, "action": w.action}


@router.post("/warnings/{warning_id}/lift", summary="End a suspension decided on a final warning (the person is active and can sign in again)")
async def lift_suspension(warning_id: uuid.UUID, payload: NoteIn, current_user: CurrentUser = Depends(decide), db=Depends(get_db)) -> dict:
    w = await WarningService(db).lift(warning_id, actor_id=current_user.id, note=payload.note)
    return {"id": w.id, "lifted_at": w.lifted_at}


@router.get("/warnings/{warning_id}/letter", summary="The warning letter (PDF)")
async def warning_letter(warning_id: uuid.UUID, current_user: CurrentUser = Depends(decide), db=Depends(get_db)) -> Response:
    parts = await WarningService(db).letter_parts(warning_id)
    return _pdf(parts, await run_in_threadpool(render_warning_letter, parts))


@router.get("/me/warnings", summary="My warnings (students and staff)")
async def my_warnings(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await WarningService(db).mine(current_user.id)


@router.post("/me/warnings/{warning_id}/acknowledge", summary="'I have read this' for one of my warnings")
async def acknowledge_warning(warning_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    w = await WarningService(db).acknowledge(warning_id, user_id=current_user.id)
    return {"id": w.id, "acknowledged_at": w.acknowledged_at}


@router.post("/me/warnings/{warning_id}/reply", summary="Reply to (appeal) one of my warnings")
async def reply_warning(warning_id: uuid.UUID, payload: ReplyIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    w = await WarningService(db).respond(warning_id, user_id=current_user.id, text=payload.text)
    return {"id": w.id, "responded_at": w.responded_at}


@router.get("/me/warnings/{warning_id}/letter", summary="The letter for one of my warnings (PDF)")
async def my_warning_letter(warning_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    parts = await WarningService(db).letter_parts(warning_id, user_id=current_user.id)
    return _pdf(parts, await run_in_threadpool(render_warning_letter, parts))
