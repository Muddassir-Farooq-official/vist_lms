"""My decisions: requests waiting for THIS person's approval, from every module (app/approvals/inbox.py)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.approvals import inbox
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user
from app.core.exceptions import ValidationAppError

router = APIRouter(prefix="/decisions", tags=["my decisions"])


class DecideIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=500)


@router.get("", summary="Everything waiting for my decision (any module, following the approval rules)")
async def mine(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await inbox.mine(db, current_user)


@router.get("/count", summary="How many requests wait for my decision (for the top bar)")
async def count(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    from app.auth.permissions import Roles

    if set(current_user.roles or []) <= {Roles.STUDENT, Roles.PARENT, Roles.ALUMNUS}:
        return {"waiting": 0}
    import time

    key, now = str(current_user.id), time.monotonic()
    hit = _COUNTS.get(key)
    if hit and now - hit[0] < 30:                       # the top bar asks on every page; 30 seconds is fresh enough
        return {"waiting": hit[1]}
    n = len(await inbox.mine(db, current_user))
    if len(_COUNTS) > 5000:
        _COUNTS.clear()
    _COUNTS[key] = (now, n)
    return {"waiting": n}


_COUNTS: dict[str, tuple[float, int]] = {}


@router.post("/{action}/{item_id}", summary="Approve or reject one request (the module's own checks apply)")
async def decide(action: str, item_id: uuid.UUID, body: DecideIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    if action not in inbox.BY_ACTION:
        raise ValidationAppError("Unknown kind of request.")
    if current_user.viewing_as_by:
        raise ValidationAppError("Viewing as someone is read-only.")
    if not body.approve and len((body.note or "").strip()) < 5:
        raise ValidationAppError("Say why it is rejected (a few words).")
    out = await inbox.decide(db, action, item_id, approve=body.approve, note=(body.note or "").strip() or None, user=current_user)
    _COUNTS.clear()
    return out
