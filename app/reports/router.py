from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.permissions import Permissions, Roles
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_any_permission, require_any_role
from app.reports.schemas import AdminDashboardResponse
from app.reports.service import ReportsService

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get(
    "/admin-dashboard",
    response_model=AdminDashboardResponse,
    summary="Admin dashboard aggregate stats (students, programs, imports, activity)",
)
async def admin_dashboard(
    current_user: CurrentUser = Depends(require_any_role(Roles.ADMIN)),
    db=Depends(get_db),
) -> AdminDashboardResponse:
    return await ReportsService(db).get_admin_dashboard()


@router.get("/institute-overview", summary="How the institute is doing right now: students, attendance, results, fees (read-only; administration and principal)")
async def institute_overview(current_user: CurrentUser = Depends(require_any_permission(Permissions.INSTITUTE_OVERVIEW)), db=Depends(get_db)) -> dict:
    from app.reports.institute_overview import InstituteOverviewService

    return await InstituteOverviewService(db).build()