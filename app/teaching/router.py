"""Teachers' classes: the day's classes with check-ins and missed attendance, the punctuality report, and class cover."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.clock import institute_today
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_permission
from app.teaching.service import TeachingService

router = APIRouter(prefix="/teaching", tags=["teaching"])
admin = require_permission(Permissions.ATTENDANCE_LOCK)


class CoverIn(BaseModel):
    substitute_id: uuid.UUID
    amount: Decimal | None = Field(default=None, ge=0, le=1_000_000)     # empty = the suggested amount


class AmountIn(BaseModel):
    amount: Decimal = Field(ge=0, le=1_000_000)


class CancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


class MarkIn(BaseModel):
    present_student_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)


def _range(first: date | None, last: date | None) -> tuple[date, date]:
    today = institute_today()
    return first or today.replace(day=1), last or today


@router.get("/day", summary="Every class on a day: teacher's check-in, attendance taken on time / late / missed, and cover")
async def day(on: date | None = None, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await TeachingService(db).day(on or institute_today())


@router.get("/punctuality", summary="Per teacher: classes due, taken on time, late, never taken, and average minutes after the start")
async def punctuality(first: date | None = None, last: date | None = None, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    a, b = _range(first, last)
    return await TeachingService(db).punctuality(a, b)


@router.get("/punctuality.csv", summary="The punctuality report as a spreadsheet (CSV)")
async def punctuality_csv(first: date | None = None, last: date | None = None, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> Response:
    a, b = _range(first, last)
    data = await TeachingService(db).punctuality(a, b)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Teacher", "Code", "Classes due", "Taken on time", "Taken late", "Never taken", "Covered for others", "On time %",
                "Average minutes after start", "Verdict"])
    for r in data["teachers"]:
        w.writerow([r["name"], r["code"], r["classes_due"], r["on_time"], r["late"], r["missed"], r["covered"],
                    "" if r["on_time_percent"] is None else r["on_time_percent"],
                    "" if r["average_minutes_after_start"] is None else r["average_minutes_after_start"], r["verdict"]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="teacher-punctuality-{a}-to-{b}.csv"'})


@router.get("/teachers/{employee_id}/classes", summary="One teacher's classes in a period and how each went")
async def teacher_classes(employee_id: uuid.UUID, first: date | None = None, last: date | None = None,
                          current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> list[dict]:
    a, b = _range(first, last)
    if (b - a) > timedelta(days=370):
        a = b - timedelta(days=370)
    return await TeachingService(db).teacher_classes(employee_id, a, b)


@router.get("/classes/{class_session_id}/cover-options", summary="Who is free to take this class, best first, and the suggested cover pay")
async def cover_options(class_session_id: uuid.UUID, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await TeachingService(db).suggestions(class_session_id)


@router.post("/classes/{class_session_id}/cover", status_code=201, summary="Give this class to another teacher or member of staff (replaces an earlier cover)")
async def assign_cover(class_session_id: uuid.UUID, payload: CoverIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await TeachingService(db).assign(class_session_id, substitute_id=payload.substitute_id, amount=payload.amount, actor_id=current_user.id)


@router.get("/covers", summary="Covers in a period: who covered what, the pay, whether the class was held, and the payroll it went on")
async def covers(first: date | None = None, last: date | None = None, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> list[dict]:
    a, b = _range(first, last)
    return await TeachingService(db).covers(a, b)


@router.patch("/covers/{cover_id}", summary="Change a cover's pay (before it is on a payroll)")
async def cover_amount(cover_id: uuid.UUID, payload: AmountIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> dict:
    return await TeachingService(db).set_amount(cover_id, amount=payload.amount, actor_id=current_user.id)


@router.post("/covers/{cover_id}/cancel", status_code=204, summary="Take a cover back (before it is on a payroll)")
async def cancel_cover(cover_id: uuid.UUID, payload: CancelIn, current_user: CurrentUser = Depends(admin), db=Depends(get_db)) -> Response:
    await TeachingService(db).cancel(cover_id, reason=payload.reason or "", actor_id=current_user.id)
    return Response(status_code=204)


# ---------------------------------------------------------------------- the person covering
@router.get("/covers/me", summary="Classes I am covering (last week to next month)")
async def my_covers(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await TeachingService(db).mine(current_user.id)


@router.get("/covers/me/{cover_id}/register", summary="The register of a class I am covering")
async def my_register(cover_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await TeachingService(db).register(cover_id, current_user.id)


@router.post("/covers/me/{cover_id}/mark", summary="Take the attendance of a class I am covering (same window as its own teacher)")
async def my_mark(cover_id: uuid.UUID, payload: MarkIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await TeachingService(db).mark(cover_id, current_user.id, payload.present_student_ids)