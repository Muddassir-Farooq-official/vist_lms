from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends

from app.auth.permissions import Roles
from app.auth.schemas import CurrentUser
from app.core.cache import get_cached, refresh_in_background, set_cached
from app.core.database import AsyncSessionLocal, get_db
from app.core.dependencies import require_any_role
from app.fees.dashboard_service import FeeDashboardService
from app.fees.schemas import FeeDashboardResponse

router = APIRouter(prefix="/fees", tags=["fees"])

_CACHE_KEY = "fee_dashboard"
_CACHE_TTL_SECONDS = 30


async def _fetch_fresh_dashboard() -> dict:
    async with AsyncSessionLocal() as session:
        return await FeeDashboardService(session).get_dashboard()


@router.get("/dashboard", response_model=FeeDashboardResponse, summary="Accountant/Admin fee dashboard")
async def fee_dashboard(
    background_tasks: BackgroundTasks,
    # NOTE — same security fix as assignment_router.py: this is
    # institution-wide financial data and must never be reachable by the
    # generic FEE_VIEW permission STUDENT also holds.
    current_user: CurrentUser = Depends(require_any_role(Roles.ADMIN, Roles.ACCOUNTS_OFFICER)),
    db=Depends(get_db),
) -> FeeDashboardResponse:
    cached = await get_cached(_CACHE_KEY)
    if cached is not None:
        background_tasks.add_task(refresh_in_background, _CACHE_KEY, _CACHE_TTL_SECONDS, _fetch_fresh_dashboard)
        return cached

    result = await FeeDashboardService(db).get_dashboard()
    await set_cached(_CACHE_KEY, result, _CACHE_TTL_SECONDS)
    return result