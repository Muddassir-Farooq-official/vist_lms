"""The principal's monthly report (app/reports/monthly.py): on screen, and the same figures as a PDF."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission
from app.core.storage import SAFE_SERVE_HEADERS
from app.reports.monthly import MonthlyReport, parse_month, render_pdf

router = APIRouter(prefix="/reports/monthly", tags=["reports"])
overview = require_permission(Permissions.INSTITUTE_OVERVIEW)


@router.get("", summary="One month's report: fees, class attendance, results, staff attendance and leave (month=YYYY-MM, default last month)")
async def monthly(month: str | None = None, current_user: CurrentUser = Depends(overview), db=Depends(get_db)) -> dict:
    return await MonthlyReport(db).build(parse_month(month))


@router.get("/pdf", summary="The same month's report as a PDF to print or share")
async def monthly_pdf(month: str | None = None, current_user: CurrentUser = Depends(overview), db=Depends(get_db)) -> Response:
    report = await MonthlyReport(db).build(parse_month(month))
    pdf = await run_in_threadpool(render_pdf, report)
    name = f"monthly-report-{report['month']:%Y-%m}.pdf"
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store", **SAFE_SERVE_HEADERS})
