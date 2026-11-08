from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.auth.schemas import CurrentUser
from app.core.dependencies import get_current_user
from app.core.releases import release_notes, version_info

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/version", summary="Which version is running (public)")
async def get_version() -> dict:
    return version_info()


@router.get("/releases", summary="Release notes, newest first (signed-in users)")
async def get_releases(limit: int | None = Query(None, ge=1, le=100), _: CurrentUser = Depends(get_current_user)) -> dict:
    return {**version_info(), "releases": release_notes(limit)}
