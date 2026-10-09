"""Policy settings an administrator can change from the Settings page (Step 5)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission
from app.policy import models as _models  # noqa: F401  (registers the table)
from app.policy import service

router = APIRouter(prefix="/policy", tags=["policy"])


class PolicyUpdate(BaseModel):
    """{key: new value}. A value of null puts that setting back to its default."""

    values: dict[str, float | int | None]


@router.get("", summary="Current institute policy values, their defaults and allowed ranges (administrator)")
async def get_policy(current_user: CurrentUser = Depends(require_permission(Permissions.POLICY_MANAGE)), db=Depends(get_db)) -> list[dict]:
    await service.apply_from_db(db)
    return service.current()


@router.put("", summary="Change policy values; takes effect immediately, audited (administrator)")
async def update_policy(payload: PolicyUpdate, current_user: CurrentUser = Depends(require_permission(Permissions.POLICY_MANAGE)),
                        db=Depends(get_db)) -> list[dict]:
    return await service.update(db, payload.values, actor_id=current_user.id)