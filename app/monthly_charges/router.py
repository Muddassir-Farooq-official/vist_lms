"""Monthly charges API: the hostel or transport office prepares a draft, the accounts office approves it."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission
from app.core.exceptions import AuthorizationError

router = APIRouter(prefix="/monthly-charges", tags=["monthly charges"])
approve_right = require_permission(Permissions.CHARGES_APPROVE)
PREPARE = {"HOSTEL": Permissions.HOSTEL_MANAGE, "TRANSPORT": Permissions.TRANSPORT_MANAGE}


def _svc(db):
    from app.monthly_charges.service import MonthlyChargeService

    return MonthlyChargeService(db)


def _may_see(user: CurrentUser, kind: str | None) -> bool:
    if Permissions.CHARGES_APPROVE in user.permissions:
        return True
    return kind in PREPARE and PREPARE[kind] in user.permissions


class PrepareIn(BaseModel):
    kind: str = Field(pattern="^(HOSTEL|TRANSPORT)$")
    month: date


@router.post("/prepare", summary="Work out the month's charges as a draft for the accounts office (nothing is charged yet)")
async def prepare(payload: PrepareIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    if PREPARE[payload.kind] not in current_user.permissions:
        raise AuthorizationError("This is for the hostel or transport office.")
    return await _svc(db).prepare(payload.kind, payload.month, actor_id=current_user.id)


@router.get("", summary="Drafts and decided months, newest first")
async def runs(status: str | None = None, kind: str | None = None, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    if not _may_see(current_user, kind):
        raise AuthorizationError("You cannot see these charges.")
    return await _svc(db).runs(status=status, kind=kind)


@router.get("/{run_id}", summary="A draft with every line")
async def detail(run_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    out = await _svc(db).detail(run_id)
    if not _may_see(current_user, out["kind"]):
        raise AuthorizationError("You cannot see these charges.")
    return out


@router.post("/{run_id}/approve", summary="Approve: add every line to the students' dues")
async def approve(run_id: uuid.UUID, current_user: CurrentUser = Depends(approve_right), db=Depends(get_db)) -> dict:
    return await _svc(db).approve(run_id, actor_id=current_user.id)


class DiscardIn(BaseModel):
    note: str = Field(min_length=3, max_length=300)


@router.post("/{run_id}/discard", summary="Send a draft back (the office corrects and prepares it again)")
async def discard(run_id: uuid.UUID, payload: DiscardIn, current_user: CurrentUser = Depends(approve_right), db=Depends(get_db)) -> dict:
    return await _svc(db).discard(run_id, note=payload.note, actor_id=current_user.id)
