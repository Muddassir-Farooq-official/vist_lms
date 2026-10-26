"""Reports centre API: the list of reports this person may run, running one, and the same result as a CSV."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user
from app.core.exceptions import AuthorizationError
from app.reports.center import ReportCenter

router = APIRouter(prefix="/report-center", tags=["reports"])


def _params(request: Request) -> dict:
    return {k: v for k, v in request.query_params.items() if v not in (None, "")}


@router.get("/catalog", summary="The reports this person may run, grouped, with their filters and filter choices")
async def catalog(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    out = await ReportCenter(db).catalog(current_user)
    if not out["groups"]:
        raise AuthorizationError("You do not have access to any report.")
    return out


@router.get("/run/{key}", summary="Run one report")
async def run(key: str, request: Request, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await ReportCenter(db).run(key, _params(request), current_user)


def _cell(v) -> str:
    text = "" if v is None else str(v)
    return "'" + text if text[:1] in ("=", "+", "-", "@") and not text.lstrip("-").replace(".", "", 1).isdigit() else text   # no spreadsheet formulas


@router.get("/export/{key}", summary="Download one report as a spreadsheet (same figures as on screen)")
async def run_csv(key: str, request: Request, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    r = await ReportCenter(db).run(key, _params(request), current_user)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([r["title"]] + ([r["subtitle"]] if r.get("subtitle") else []))
    cols = r["columns"]
    w.writerow([c["label"] for c in cols])
    for row in r["rows"]:
        w.writerow([_cell(row.get(c["key"])) for c in cols])
    if r.get("totals"):
        w.writerow([("Total" if i == 0 else _cell(r["totals"].get(c["key"], ""))) for i, c in enumerate(cols)])
    for n in r.get("notes") or []:
        w.writerow([n])
    name = key.replace(".", "-") + "-" + str(r["generated_on"]) + ".csv"
    return Response(content="﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"})
