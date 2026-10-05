from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.library.dashboard_service import LibraryDashboardService
from app.library.schemas import LibraryDashboardResponse

router = APIRouter(prefix="/library", tags=["library"])


@router.get("/dashboard", response_model=LibraryDashboardResponse, summary="Library dashboard stats")
async def library_dashboard(
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_VIEW)),
    db=Depends(get_db),
) -> LibraryDashboardResponse:
    return await LibraryDashboardService(db).get_dashboard()