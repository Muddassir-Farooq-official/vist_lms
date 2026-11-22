"""Committees API (app/committees/service.py): members and administrators only."""
from __future__ import annotations

import uuid
from datetime import date, time

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission

router = APIRouter(prefix="/committees", tags=["committees"])
office = require_permission(Permissions.COMMITTEE_MANAGE)


def _svc(db):
    from app.committees.service import CommitteeService

    return CommitteeService(db)


def _admin(user: CurrentUser) -> bool:
    return Permissions.COMMITTEE_MANAGE in user.permissions


class MemberIn(BaseModel):
    user_id: uuid.UUID | None = None
    employee_code: str | None = Field(default=None, max_length=20)
    role: str = Field(default="MEMBER", pattern="^(CHAIR|SECRETARY|MEMBER)$")


class CommitteeIn(BaseModel):
    name: str = Field(max_length=120)
    purpose: str | None = Field(default=None, max_length=1000)
    is_active: bool | None = None
    members: list[MemberIn] | None = Field(default=None, max_length=100)


async def _resolve(db, payload: CommitteeIn) -> dict:
    from sqlalchemy import func, select

    from app.core.exceptions import ValidationAppError
    from app.employees.models import Employee

    data = payload.model_dump(exclude={"members"})
    if payload.members is not None:
        out = []
        for m in payload.members:
            uid = m.user_id
            if uid is None and m.employee_code:
                uid = (await db.execute(select(Employee.user_id).where(func.upper(Employee.employee_code) == m.employee_code.strip().upper()))).scalar_one_or_none()
                if uid is None:
                    raise ValidationAppError(f"No employee with a login has code {m.employee_code}.")
            if uid is None:
                raise ValidationAppError("Each member needs an employee code.")
            out.append({"user_id": uid, "role": m.role})
        data["members"] = out
    return data


@router.get("", summary="Committees I belong to (administrators: all)")
async def committees(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).committees(current_user, admin=_admin(current_user))


@router.post("", status_code=201, summary="Make a committee with its members (administration)")
async def add(payload: CommitteeIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).save(await _resolve(db, payload), committee_id=None, actor_id=current_user.id)


@router.put("/{committee_id}", summary="Change a committee, its members, or close it (administration)")
async def edit(committee_id: uuid.UUID, payload: CommitteeIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).save(await _resolve(db, payload), committee_id=committee_id, actor_id=current_user.id)


@router.get("/actions/mine", summary="Action items given to me")
async def my_actions(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).my_actions(current_user)


@router.get("/actions/open", summary="Every open action item (administration)")
async def open_actions(current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).all_open_actions()


class MeetingIn(BaseModel):
    held_on: date
    start_time: time | None = None
    place: str | None = Field(default=None, max_length=120)
    agenda: str = Field(max_length=20000)


@router.get("/{committee_id}/meetings", summary="A committee's meetings (members)")
async def meetings(committee_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).meetings(committee_id, current_user, admin=_admin(current_user))


@router.post("/{committee_id}/meetings", status_code=201, summary="Schedule a meeting (chair or secretary; members are told)")
async def schedule(committee_id: uuid.UUID, payload: MeetingIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await _svc(db).schedule(committee_id, payload.model_dump(), user=current_user, admin=_admin(current_user))


@router.get("/meetings/{meeting_id}", summary="A meeting: agenda, minutes, decisions, actions (members)")
async def meeting(meeting_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await _svc(db).meeting(meeting_id, current_user, admin=_admin(current_user))


class MinutesIn(BaseModel):
    held_on: date | None = None
    start_time: time | None = None
    place: str | None = Field(default=None, max_length=120)
    agenda: str | None = Field(default=None, max_length=20000)
    minutes: str | None = Field(default=None, max_length=20000)
    decisions: str | None = Field(default=None, max_length=20000)
    attended: str | None = Field(default=None, max_length=2000)


@router.put("/meetings/{meeting_id}", summary="Write or correct the minutes (chair or secretary, until final)")
async def write_minutes(meeting_id: uuid.UUID, payload: MinutesIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await _svc(db).write_minutes(meeting_id, payload.model_dump(exclude_unset=True), user=current_user, admin=_admin(current_user))


@router.post("/meetings/{meeting_id}/final", summary="Make the minutes final (the chair); they can no longer change")
async def finalise(meeting_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await _svc(db).finalise(meeting_id, user=current_user, admin=_admin(current_user))


@router.post("/meetings/{meeting_id}/cancel", summary="Cancel a meeting that has no actions")
async def cancel(meeting_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await _svc(db).cancel(meeting_id, user=current_user, admin=_admin(current_user))


@router.get("/meetings/{meeting_id}/pdf", summary="The minutes as a PDF (members)")
async def minutes_pdf(meeting_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    body = await _svc(db).minutes_pdf(meeting_id, current_user, admin=_admin(current_user))
    return Response(content=body, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="minutes.pdf"', "Cache-Control": "no-store"})


class ActionIn(BaseModel):
    title: str = Field(max_length=300)
    owner_id: uuid.UUID | None = None
    owner_code: str | None = Field(default=None, max_length=20)
    due_on: date


@router.post("/meetings/{meeting_id}/actions", status_code=201, summary="Give someone an action (chair or secretary)")
async def add_action(meeting_id: uuid.UUID, payload: ActionIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    from sqlalchemy import func, select

    from app.core.exceptions import ValidationAppError
    from app.employees.models import Employee

    owner = payload.owner_id
    if owner is None and payload.owner_code:
        owner = (await db.execute(select(Employee.user_id).where(func.upper(Employee.employee_code) == payload.owner_code.strip().upper()))).scalar_one_or_none()
    if owner is None:
        raise ValidationAppError("Choose who does it (an employee with a login).")
    return await _svc(db).add_action(meeting_id, title=payload.title, owner_id=owner, due_on=payload.due_on, user=current_user, admin=_admin(current_user))


class ActionUpdateIn(BaseModel):
    status: str = Field(pattern="^(OPEN|DONE|DROPPED)$")
    note: str | None = Field(default=None, max_length=500)
    due_on: date | None = None


@router.post("/actions/{action_id}", summary="Mark an action done (its owner), or drop / move it (chair or secretary)")
async def update_action(action_id: uuid.UUID, payload: ActionUpdateIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await _svc(db).update_action(action_id, status=payload.status, note=payload.note, due_on=payload.due_on, user=current_user, admin=_admin(current_user))
