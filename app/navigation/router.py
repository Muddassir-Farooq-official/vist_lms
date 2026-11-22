"""My shortcuts: the pages a person starred, per portal. Only ever the person's own list."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user
from app.core.exceptions import ValidationAppError
from app.navigation.models import NavShortcut

router = APIRouter(prefix="/nav", tags=["navigation"])
MAX = 12
_PORTAL = re.compile(r"^[a-z][a-z0-9-]{0,19}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def _portal(portal: str) -> str:
    if not _PORTAL.match(portal or ""):
        raise ValidationAppError("Unknown portal.")
    return portal


class ShortcutsIn(BaseModel):
    portal: str
    slugs: list[str] = Field(default_factory=list, max_length=MAX)


@router.get("/shortcuts", summary="The pages I starred in this portal's menu, in order")
async def my_shortcuts(portal: str = Query(...), current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    rows = (await db.execute(select(NavShortcut.slug).where(NavShortcut.user_id == current_user.id, NavShortcut.portal == _portal(portal))
                             .order_by(NavShortcut.position, NavShortcut.created_at))).scalars().all()
    return {"portal": portal, "slugs": list(rows), "max": MAX}


@router.put("/shortcuts", summary="Replace my starred pages for this portal (at most 12)")
async def save_shortcuts(payload: ShortcutsIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    portal = _portal(payload.portal)
    slugs = list(dict.fromkeys(payload.slugs))
    if any(not _SLUG.match(s) for s in slugs):
        raise ValidationAppError("Unknown page.")
    await db.execute(delete(NavShortcut).where(NavShortcut.user_id == current_user.id, NavShortcut.portal == portal))
    for i, slug in enumerate(slugs):
        db.add(NavShortcut(user_id=current_user.id, portal=portal, slug=slug, position=i))
    await db.flush()
    return {"portal": portal, "slugs": slugs, "max": MAX}
