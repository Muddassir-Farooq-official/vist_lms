from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends

from app.auth.permissions import Permissions
from app.core.clock import institute_today
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission
from app.payroll import rules
from app.performance.service import PerformanceService

router = APIRouter(prefix="/performance", tags=["performance (administration)"])


@router.get("", summary="Staff scorecard for a month (default: last month): attendance, punctuality, tasks and conduct, ranked")
async def scorecard(month: date | None = None, current_user: CurrentUser = Depends(require_permission(Permissions.BONUS_MANAGE)), db=Depends(get_db)) -> dict:
    if month is None:
        month = (rules.month_start(institute_today()) - timedelta(days=1)).replace(day=1)
    return await PerformanceService(db).scorecard(rules.month_start(month))