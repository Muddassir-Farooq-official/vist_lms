"""Activity log → Security: refused and attack-looking attempts, who made them and from where (administration and principal)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.security import service

router = APIRouter(prefix="/security", tags=["security"])

KIND_CHOICES = ["FAILED_SIGNIN", *service.KINDS.keys()]


@router.get("/summary", summary="Counts, the addresses behind the most trouble, and the accounts with the most wrong passwords")
async def security_summary(days: int = Query(7, ge=1, le=90), current_user: CurrentUser = Depends(require_permission(Permissions.AUDIT_VIEW)),
                           db=Depends(get_db)) -> dict:
    out = await service.summary(db, days=days)
    out["kinds"] = {"FAILED_SIGNIN": "Someone typed a wrong password at sign-in", **{k: v[0] for k, v in service.KINDS.items()}}
    return out


@router.get("/events", summary="Security events, newest first (failed sign-ins included)")
async def security_events(days: int = Query(7, ge=1, le=90), kind: str | None = Query(None, max_length=30), ip: str | None = Query(None, max_length=64),
                          q: str | None = Query(None, max_length=100), current_user: CurrentUser = Depends(require_permission(Permissions.AUDIT_VIEW)),
                          db=Depends(get_db)) -> list[dict]:
    if kind and kind not in KIND_CHOICES:
        kind = None
    return await service.events(db, days=days, kind=kind, ip=(ip or "").strip() or None, q=(q or "").strip() or None)

# ---------------------------------------------------------------------------
# Two-step sign-in for administrators (app/auth/two_factor.py)
# ---------------------------------------------------------------------------


class _ResetIn(BaseModel):
    reason: str = Field(min_length=5, max_length=300)


@router.get("/two-factor", summary="Administrators and whether each has set up two-step sign-in")
async def two_factor_accounts(current_user: CurrentUser = Depends(require_permission(Permissions.AUDIT_VIEW)), db=Depends(get_db)) -> dict:
    from sqlalchemy import select

    from app.auth import two_factor as tf
    from app.auth.models import Role, User, UserRole
    from app.auth.permissions import Roles
    from app.core.config import settings

    users = (await db.execute(select(User).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id)
                              .where(Role.code == Roles.ADMIN).order_by(User.username))).scalars().unique().all()
    return {"required": tf.required_for([Roles.ADMIN]), "remember_days": int(settings.two_factor_remember_days),
            "can_reset": Permissions.ROLE_MANAGE in (current_user.permissions or []),
            "accounts": [{"user_id": u.id, "username": u.username, "active": u.is_active, "set_up": u.totp_secret_enc is not None,
                          "set_up_at": u.totp_enabled_at, "last_login_at": u.last_login_at, "is_me": u.id == current_user.id} for u in users]}


@router.post("/two-factor/{user_id}/reset", summary="Lost phone: turn two-step sign-in off for someone, who sets it up again at the next sign-in")
async def reset_two_factor(user_id: uuid.UUID, payload: _ResetIn, current_user: CurrentUser = Depends(require_permission(Permissions.ROLE_MANAGE)),
                           db=Depends(get_db)) -> dict:
    from app.auth import two_factor as tf
    from app.auth.models import User
    from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
    from app.notifications.models import NotificationCategory
    from app.notifications.service import create_notification

    if user_id == current_user.id:
        raise BusinessRuleViolationError("Another administrator has to reset your two-step sign-in (or it is reset on the server).")
    user = await db.get(User, user_id)
    if user is None:
        raise ResourceNotFoundError("Account not found.")
    await tf.reset(db, user, actor_id=current_user.id, how=f"by {current_user.username}: {payload.reason.strip()}")
    await create_notification(db, user_id=user.id, category=NotificationCategory.GENERAL,
                              title="Two-step sign-in was reset",
                              message=f"{current_user.username} reset your two-step sign-in. At your next sign-in you will set it up again with your phone.")
    return {"user_id": user.id, "set_up": False}
