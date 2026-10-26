from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.approvals.service import ApprovalService
from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission

router = APIRouter(prefix="/approvals", tags=["approvals"])
decide_perm = require_permission(Permissions.APPROVALS_DECIDE)


class DecisionIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=300)


@router.get("", summary="Approval requests (approvers see all; anyone else sees the ones they asked for)")
async def listing(status: str | None = Query(default="REQUESTED", pattern="^(REQUESTED|APPROVED|REJECTED|CANCELLED)?$"),
                  current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    mine = None if Permissions.APPROVALS_DECIDE in current_user.permissions else current_user.id
    return await ApprovalService(db).listing(status=status or None, mine_for=mine)


@router.get("/count", summary="How many requests are waiting (for the menu badge)")
async def count(current_user: CurrentUser = Depends(decide_perm), db=Depends(get_db)) -> dict:
    return {"waiting": await ApprovalService(db).pending_count()}


@router.post("/{request_id}/decide", summary="Approve (carries the action out) or reject a request; never your own")
async def decide(request_id: uuid.UUID, body: DecisionIn, current_user: CurrentUser = Depends(decide_perm), db=Depends(get_db)) -> dict:
    r = await ApprovalService(db).decide(request_id, approve=body.approve, note=body.note, actor_id=current_user.id)
    return {"id": r.id, "status": r.status, "result": r.result}


@router.post("/{request_id}/cancel", summary="Withdraw a request you made that is still waiting")
async def cancel(request_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    r = await ApprovalService(db).cancel(request_id, actor_id=current_user.id)
    return {"id": r.id, "status": r.status}
