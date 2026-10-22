from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions, Roles
from app.auth.schemas import CurrentUser
from app.calendar import models as _models  # noqa: F401  (registers the table)
from app.calendar.service import CalendarService
from app.core.clock import institute_today
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_permission

router = APIRouter(prefix="/calendar", tags=["calendar"])
manage = require_permission(Permissions.CALENDAR_MANAGE)
STUDENT_SIDE = {Roles.STUDENT, Roles.PARENT}


class EventIn(BaseModel):
    title: str = Field(min_length=3, max_length=150)
    kind: str = Field(default="EVENT", pattern="^(HOLIDAY|EXAM|EVENT|TERM|DEADLINE)$")
    start_date: date
    end_date: date | None = None
    details: str | None = Field(default=None, max_length=1000)
    for_students: bool = True
    for_staff: bool = True


@router.get("", summary="Calendar entries between two dates (what this person should see)")
async def events(start: date | None = None, end: date | None = None, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    today = institute_today()
    start = start or today.replace(day=1)
    end = end or (start + timedelta(days=95))
    if (end - start).days > 400:
        end = start + timedelta(days=400)
    roles = set(current_user.roles or [])
    audience = None if Roles.ADMIN in roles else ("STUDENTS" if roles & STUDENT_SIDE and not (roles - STUDENT_SIDE) else "STAFF")
    return await CalendarService(db).list(start=start, end=end, audience=audience)


@router.post("", status_code=201, summary="Add a holiday, exam, event, term date or deadline. A holiday cancels classes / excuses staff days")
async def add(payload: EventIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    data = payload.model_dump()
    data["end_date"] = data["end_date"] or data["start_date"]
    return await CalendarService(db).create(data, actor_id=current_user.id)


@router.delete("/{event_id}", status_code=204, summary="Remove a calendar entry (cancelled classes and excused staff days stay as they are)")
async def remove(event_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> None:
    await CalendarService(db).delete(event_id, actor_id=current_user.id)