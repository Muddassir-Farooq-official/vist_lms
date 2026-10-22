from __future__ import annotations

import uuid
from datetime import date, time

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission
from app.gate.service import GateService

router = APIRouter(prefix="/gate", tags=["gate"])
operate = require_permission(Permissions.GATE_OPERATE)
manage = require_permission(Permissions.GATE_MANAGE)


def _admin(user: CurrentUser) -> bool:
    return Permissions.GATE_MANAGE in user.permissions


class VisitorIn(BaseModel):
    visitor_name: str = Field(min_length=2, max_length=150)
    cnic: str | None = Field(default=None, max_length=20)
    phone: str | None = Field(default=None, max_length=20)
    coming_from: str | None = Field(default=None, max_length=150)
    purpose: str = Field(min_length=2, max_length=200)
    person_to_meet: str | None = Field(default=None, max_length=150)
    vehicle_no: str | None = Field(default=None, max_length=30)
    people_count: int = Field(default=1, ge=1, le=200)
    card_no: str | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=300)


class VisitorEdit(BaseModel):
    visitor_name: str | None = Field(default=None, max_length=150)
    cnic: str | None = Field(default=None, max_length=20)
    phone: str | None = Field(default=None, max_length=20)
    coming_from: str | None = Field(default=None, max_length=150)
    purpose: str | None = Field(default=None, max_length=200)
    person_to_meet: str | None = Field(default=None, max_length=150)
    vehicle_no: str | None = Field(default=None, max_length=30)
    people_count: int | None = Field(default=None, ge=1, le=200)
    card_no: str | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=300)
    entry_time: time | None = None
    exit_time: time | None = None


@router.get("/summary", summary="Visitors today and inside now")
async def summary(current_user: CurrentUser = Depends(operate), db=Depends(get_db)) -> dict:
    return await GateService(db).summary()


@router.get("/inside", summary="Visitors who have not left yet")
async def inside(current_user: CurrentUser = Depends(operate), db=Depends(get_db)) -> list[dict]:
    return await GateService(db).inside()


@router.post("/visitors", status_code=201, summary="A visitor comes in")
async def enter(payload: VisitorIn, current_user: CurrentUser = Depends(operate), db=Depends(get_db)) -> dict:
    return await GateService(db).enter(payload.model_dump(), actor_id=current_user.id)


@router.post("/visitors/{entry_id}/out", summary="A visitor leaves")
async def leave(entry_id: uuid.UUID, current_user: CurrentUser = Depends(operate), db=Depends(get_db)) -> dict:
    return await GateService(db).leave(entry_id, actor_id=current_user.id)


@router.patch("/visitors/{entry_id}", summary="Correct an entry (administration)")
async def edit(entry_id: uuid.UUID, payload: VisitorEdit, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await GateService(db).edit(entry_id, payload.model_dump(exclude_unset=True), actor_id=current_user.id)


@router.get("/visitors", summary="The log book: search by name, CNIC, phone, purpose or vehicle (a guard sees the last 7 days)")
async def search(q: str | None = None, from_date: date | None = None, to_date: date | None = None, inside_only: bool = False, limit: int = 200, offset: int = 0,
                 current_user: CurrentUser = Depends(operate), db=Depends(get_db)) -> list[dict]:
    return await GateService(db).search(q=q, from_date=from_date, to_date=to_date, inside_only=inside_only, limit=limit, offset=offset, admin=_admin(current_user))


@router.get("/visitors/by-cnic/{cnic}", summary="A returning visitor's details from their last visit")
async def by_cnic(cnic: str, current_user: CurrentUser = Depends(operate), db=Depends(get_db)) -> dict | None:
    return await GateService(db).last_visit(cnic)


@router.get("/visitors.csv", summary="Download the log book as CSV (administration)")
async def export(q: str | None = None, from_date: date | None = None, to_date: date | None = None,
                 current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> Response:
    body = await GateService(db).export_csv(q=q, from_date=from_date, to_date=to_date)
    return Response(content=body, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="visitor-log.csv"'})