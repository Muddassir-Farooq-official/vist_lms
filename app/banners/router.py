"""Top bars: what the signed-in person should see, and the administration screens to manage them."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.banners.service import ROLE_LABELS, STAFF_ROLES, BannerService
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_permission

router = APIRouter(prefix="/banners", tags=["banners"])
manage = require_permission(Permissions.ANNOUNCEMENT_MANAGE)


class BannerIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    message: str | None = Field(default=None, max_length=300)
    button_label: str | None = Field(default=None, max_length=40)
    button_url: str | None = Field(default=None, max_length=500)
    new_tab: bool | None = None
    tone: str | None = Field(default=None, max_length=10)
    audience: dict | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_active: bool | None = None
    dismissible: bool | None = None
    position: int | None = Field(default=None, ge=0, le=999)


# ------------------------------------------------------------------ everyone
@router.get("/mine", summary="The top bars the signed-in person should see now (at most three)")
async def my_banners(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await BannerService(db).mine(current_user.id, current_user.roles)


@router.post("/{banner_id}/click", status_code=204, summary="I pressed this bar's button (recorded so the administration sees who responded)")
async def banner_click(banner_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    await BannerService(db).clicked(banner_id, current_user.id)
    return Response(status_code=204)


@router.post("/{banner_id}/dismiss", status_code=204, summary="Close this bar for me (only bars that allow it)")
async def banner_dismiss(banner_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    await BannerService(db).dismissed(banner_id, current_user.id, current_user.roles)
    return Response(status_code=204)


# ------------------------------------------------------------------ administration
@router.get("", summary="Every top bar with its status, reach and responses (administration)")
async def list_banners(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return {"bars": await BannerService(db).listing(), "roles": [{"code": k, "label": v} for k, v in ROLE_LABELS.items()], "staff_roles": STAFF_ROLES}


@router.post("", status_code=201, summary="Create a top bar (administration)")
async def create_banner(payload: BannerIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await BannerService(db).create(payload.model_dump(exclude_unset=True), actor_id=current_user.id)


@router.get("/people", summary="Find students and staff to add as named people (administration)")
async def find_people(q: str = Query("", max_length=60), current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    return await BannerService(db).people(q)


class ReachIn(BaseModel):
    audience: dict


@router.post("/reach", summary="How many accounts an audience reaches, before saving (administration)")
async def audience_reach(payload: ReachIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    from app.banners.service import clean_audience
    from app.core.exceptions import ValidationAppError

    service = BannerService(db)
    try:
        audience = clean_audience(payload.audience)
    except ValidationAppError:
        return {"reach": 0, "label": "Nobody yet"}
    return {"reach": await service._audience_size(audience), "label": await service._audience_label(audience)}


class NamesIn(BaseModel):
    user_ids: list[str] = Field(default_factory=list, max_length=3000)


@router.post("/people/names", summary="Names for a list of people already chosen (administration)")
async def people_names(payload: NamesIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    return await BannerService(db).names(payload.user_ids)


@router.patch("/{banner_id}", summary="Change a top bar, or show / hide it (administration)")
async def update_banner(banner_id: uuid.UUID, payload: BannerIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await BannerService(db).update(banner_id, payload.model_dump(exclude_unset=True), actor_id=current_user.id)


@router.delete("/{banner_id}", status_code=204, summary="Delete a top bar and its responses (administration)")
async def delete_banner(banner_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> Response:
    await BannerService(db).delete(banner_id, actor_id=current_user.id)
    return Response(status_code=204)


@router.get("/{banner_id}/responses", summary="Who pressed the button or closed the bar (administration)")
async def banner_responses(banner_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    return await BannerService(db).responses(banner_id)


@router.get("/{banner_id}/responses.csv", summary="Download who responded, as CSV (administration)")
async def banner_responses_csv(banner_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> Response:
    body, name = await BannerService(db).responses_csv(banner_id)
    return Response(content=body, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})