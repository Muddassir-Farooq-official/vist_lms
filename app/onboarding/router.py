"""Onboarding: the administrator's setup checklist, each person's own to-do list, and page help for the guided tours."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.permissions import Roles
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.exceptions import AuthorizationError
from app.onboarding.service import OnboardingService, page_help

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])


@router.get("/setup", summary="Set up your LMS: the steps in order, each done or not (administrator)")
async def setup(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    if Roles.ADMIN not in current_user.roles:
        raise AuthorizationError("Only the administrator sets up the LMS.")
    return await OnboardingService(db).setup()


@router.get("/me", summary="What I still have to do: my profile, and today's important things")
async def me(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    if current_user.viewing_as_by:
        return {"profile": None, "todo": [], "grace_days_left": None, "must_finish": False}
    return await OnboardingService(db).me(current_user.id, current_user.roles)


@router.get("/page-help", summary="The manual's words for a page of a portal (for the guided tours)")
async def help_for_page(portal: str, label: str, current_user: CurrentUser = Depends(get_current_user)) -> dict:
    return page_help(portal[:20], label[:80]) or {}
