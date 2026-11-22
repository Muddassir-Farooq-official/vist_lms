"""Student mentoring API (app/mentoring/service.py). Notes are read only by the student's mentor and administrators."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission

router = APIRouter(prefix="/mentoring", tags=["mentoring"])
office = require_permission(Permissions.MENTORING_MANAGE)


def _svc(db):
    from app.mentoring.service import MentoringService

    return MentoringService(db)


def _admin(user: CurrentUser) -> bool:
    return Permissions.MENTORING_MANAGE in user.permissions


# ---------------------------------------------------------------- administration
@router.get("/overview", summary="Current students with their mentors, and each mentor's number of mentees")
async def overview(program_id: uuid.UUID | None = None, semester: int | None = None, unassigned: bool = False,
                   current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).overview(program_id=program_id, semester=semester, unassigned_only=unassigned)


class AssignIn(BaseModel):
    student_ids: list[uuid.UUID] = Field(min_length=1, max_length=3000)
    mentor_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    mentor_codes: list[str] = Field(default_factory=list, max_length=200)      # or the teachers' employee codes
    only_unassigned: bool = True


@router.post("/assign", summary="Give students a mentor (several mentors: shared out evenly)")
async def assign(payload: AssignIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    from sqlalchemy import func, select

    from app.core.exceptions import ValidationAppError
    from app.employees.models import Employee

    ids = list(payload.mentor_ids)
    codes = [c.strip() for c in payload.mentor_codes if c.strip()]
    if codes:
        found = dict((await db.execute(select(func.upper(Employee.employee_code), Employee.id).where(func.upper(Employee.employee_code).in_([c.upper() for c in codes])))).all())
        missing = [c for c in codes if c.upper() not in found]
        if missing:
            raise ValidationAppError("No employee with code: " + ", ".join(missing))
        ids += list(found.values())
    return await _svc(db).assign(student_ids=payload.student_ids, mentor_ids=list(dict.fromkeys(ids)), only_unassigned=payload.only_unassigned,
                                 actor_id=current_user.id)


@router.delete("/students/{student_id}/mentor", summary="End a student's mentor (their notes stay)")
async def unassign(student_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    await _svc(db).unassign(student_id, actor_id=current_user.id)
    return {"ended": True}


@router.get("/referrals", summary="Students referred to the principal by their mentor")
async def referrals(status: str = "OPEN", current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).referrals(status=status)


class ResolveIn(BaseModel):
    resolution: str = Field(min_length=5, max_length=1000)


@router.post("/referrals/{meeting_id}/resolve", summary="Answer a referral (the mentor is told)")
async def resolve(meeting_id: uuid.UUID, payload: ResolveIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).resolve(meeting_id, resolution=payload.resolution, actor_id=current_user.id)


# ---------------------------------------------------------------- the mentor (and administrators)
@router.get("/mentees", summary="My mentees with warning signs and follow-ups due")
async def mentees(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).mentees(current_user)


@router.get("/students/{student_id}", summary="A mentee's file: warning signs and meetings (mentor or administrator)")
async def student_file(student_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await _svc(db).student_file(student_id, current_user, admin=_admin(current_user))


class MeetingIn(BaseModel):
    met_on: date | None = None
    kind: str = Field(default="MEETING", pattern="^(MEETING|PHONE|PARENTS)$")
    notes: str = Field(min_length=1, max_length=20000)
    actions: str | None = Field(default=None, max_length=1000)
    follow_up_on: date | None = None
    refer: bool = False
    referral_reason: str | None = Field(default=None, max_length=500)


@router.post("/students/{student_id}/meetings", status_code=201, summary="Record a meeting (the mentor)")
async def add_meeting(student_id: uuid.UUID, payload: MeetingIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await _svc(db).add_meeting(student_id, payload.model_dump(), user=current_user, admin=_admin(current_user))


class MeetingEditIn(BaseModel):
    notes: str | None = Field(default=None, max_length=20000)
    actions: str | None = Field(default=None, max_length=1000)
    follow_up_on: date | None = None
    remove: bool = False


@router.put("/meetings/{meeting_id}", summary="Correct my note within a week, or hide one written on the wrong student")
async def edit_meeting(meeting_id: uuid.UUID, payload: MeetingEditIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await _svc(db).edit_meeting(meeting_id, payload.model_dump(exclude_unset=True), user=current_user)
