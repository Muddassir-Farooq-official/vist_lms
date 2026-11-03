"""Daily to-do lists: my own list (every member of staff) and the monthly report (administration)."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.clock import institute_today
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.todos.service import TodoService

router = APIRouter(prefix="/todos", tags=["todos"])
use = require_permission(Permissions.TASK_USE)
manage = require_permission(Permissions.TASK_MANAGE)
Month = Query(None, pattern=r"^\d{4}-\d{2}$", description="e.g. 2026-11 (default: this month)")


class TodoIn(BaseModel):
    work_date: date | None = None
    title: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=1000)


class TodoPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=1000)
    status: str | None = Field(default=None, max_length=15)


class CarryIn(BaseModel):
    to_date: date | None = None


@router.get("/me", summary="My to-do list for one day (default today)")
async def my_day(day: date | None = None, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> dict:
    return await TodoService(db).day(current_user.id, day or institute_today())


@router.post("", status_code=201, summary="Add an item to my list (today, yesterday, or up to a month ahead)")
async def add_item(payload: TodoIn, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> dict:
    return await TodoService(db).add(current_user.id, work_date=payload.work_date or institute_today(), title=payload.title, note=payload.note)


@router.patch("/{item_id}", summary="Change an item's text, note or status (To do, In progress, Done, Not done)")
async def update_item(item_id: uuid.UUID, payload: TodoPatch, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> dict:
    return await TodoService(db).update(current_user.id, item_id, payload.model_dump(exclude_unset=True))


@router.delete("/{item_id}", status_code=204, summary="Remove an item that is not done yet (today or later)")
async def delete_item(item_id: uuid.UUID, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> Response:
    await TodoService(db).delete(current_user.id, item_id)
    return Response(status_code=204)


@router.post("/carry-over", summary="Bring the unfinished items of my latest earlier day over to today")
async def carry_over(payload: CarryIn | None = None, current_user: CurrentUser = Depends(use), db=Depends(get_db)) -> dict:
    return await TodoService(db).carry_over(current_user.id, to_date=payload.to_date if payload else None)


@router.get("/report", summary="Everyone's to-do lists for one month: days with a list, items, done, not done (administration)")
async def month_report(month: str | None = Month, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await TodoService(db).report(month)


@router.get("/report/person/{user_id}", summary="One person's lists for one month, day by day (administration)")
async def person_report(user_id: uuid.UUID, month: str | None = Month, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await TodoService(db).person_month(user_id, month)


@router.get("/report.csv", summary="Download one month of to-do items as CSV, everyone or one person (administration)")
async def month_csv(month: str | None = Month, user_id: uuid.UUID | None = None, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> Response:
    body, name = await TodoService(db).csv_month(month, user_id)
    return Response(content=body, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})