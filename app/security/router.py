"""Activity log → Security: refused and attack-looking attempts, who made them and from where (administration and principal)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

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