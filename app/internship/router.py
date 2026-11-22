"""The internship year API (app/internship/service.py)."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_any_permission, require_permission
from app.core.exceptions import AuthorizationError, ValidationAppError

router = APIRouter(prefix="/internship", tags=["internship"])
office = require_permission(Permissions.CLINICAL_MANAGE)
supervising = require_any_permission(Permissions.CLINICAL_SUPERVISE, Permissions.CLINICAL_MANAGE)


def _svc(db):
    from app.internship.service import InternshipService

    return InternshipService(db)


def _admin(user: CurrentUser) -> bool:
    return Permissions.CLINICAL_MANAGE in user.permissions


async def _student(db, user):
    from app.students.models import Student

    s = (await db.execute(select(Student).where(Student.user_id == user.id))).scalar_one_or_none()
    if s is None:
        raise AuthorizationError("This is for students.")
    return s


# ---------------------------------------------------------------- administration
class StartIn(BaseModel):
    roll_numbers: list[str] = Field(min_length=1, max_length=1000)
    start_date: date
    end_date: date
    required_hours: int = Field(ge=1, le=5000)


@router.post("/start", summary="Start the internship for students (by roll number)")
async def start(payload: StartIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    from app.students.models import Student

    rolls = [r.strip() for r in payload.roll_numbers if r.strip()]
    found = dict((await db.execute(select(Student.roll_number, Student.id).where(Student.roll_number.in_(rolls or ["-"])))).all())
    missing = [r for r in rolls if r not in found]
    if missing:
        raise ValidationAppError("Roll numbers not found: " + ", ".join(missing[:10]))
    return await _svc(db).start(student_ids=list(found.values()), start_date=payload.start_date, end_date=payload.end_date,
                                required_hours=payload.required_hours, actor_id=current_user.id)


@router.get("/interns", summary="Interns with verified hours, evaluations and leave waiting")
async def interns(status: str | None = "ONGOING", current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).interns(status=status or None)


@router.get("/interns/{internship_id}", summary="An intern's placements, duties, leave and evaluations")
async def detail(internship_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).detail(internship_id)


class PlaceIn(BaseModel):
    unit_id: uuid.UUID
    supervisor_id: uuid.UUID | None = None
    start_date: date
    end_date: date


@router.post("/interns/{internship_id}/placements", status_code=201, summary="Place an intern at a ward with a supervisor")
async def place(internship_id: uuid.UUID, payload: PlaceIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).place(internship_id, **payload.model_dump())


class DecideIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=300)


@router.post("/leaves/{leave_id}", summary="Approve or refuse an intern's leave")
async def decide_leave(leave_id: uuid.UUID, payload: DecideIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).decide_leave(leave_id, approve=payload.approve, note=payload.note, actor_id=current_user.id)


class CompleteIn(BaseModel):
    withdraw: bool = False
    note: str | None = Field(default=None, max_length=500)


@router.post("/interns/{internship_id}/complete", summary="Mark the internship complete (or withdrawn)")
async def complete(internship_id: uuid.UUID, payload: CompleteIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).complete(internship_id, withdraw=payload.withdraw, note=payload.note, actor_id=current_user.id)


# ---------------------------------------------------------------- supervisors (and administration)
@router.get("/supervise", summary="Interns placed with me, with duties to verify")
async def supervised(current_user: CurrentUser = Depends(supervising), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).supervised(current_user)


@router.post("/duties/{log_id}", summary="Verify or reject a duty")
async def verify(log_id: uuid.UUID, payload: DecideIn, current_user: CurrentUser = Depends(supervising), db=Depends(get_db)) -> dict:
    return await _svc(db).verify(log_id, approve=payload.approve, note=payload.note, user=current_user, admin=_admin(current_user))


class EvalIn(BaseModel):
    scores: dict[str, int]
    comment: str | None = Field(default=None, max_length=1000)


@router.post("/placements/{placement_id}/evaluate", status_code=201, summary="Evaluate the intern (1 to 5 on each point)")
async def evaluate(placement_id: uuid.UUID, payload: EvalIn, current_user: CurrentUser = Depends(supervising), db=Depends(get_db)) -> dict:
    return await _svc(db).evaluate(placement_id, scores=payload.scores, comment=payload.comment, user=current_user, admin=_admin(current_user))


# ---------------------------------------------------------------- the intern
@router.get("/me", summary="My internship (or null)")
async def me(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict | None:
    return await _svc(db).mine(await _student(db, current_user))


class DutyIn(BaseModel):
    day: date
    shift: str = Field(pattern="^(MORNING|EVENING|NIGHT)$")
    hours: float = Field(ge=0.5, le=16)
    note: str | None = Field(default=None, max_length=300)


@router.post("/me/duties", status_code=201, summary="Log a duty for verification")
async def log_duty(payload: DutyIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await _svc(db).log_duty(await _student(db, current_user), **payload.model_dump())


@router.delete("/me/duties/{log_id}", summary="Withdraw a duty not yet verified")
async def withdraw_duty(log_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    await _svc(db).withdraw_log(await _student(db, current_user), log_id)
    return {"deleted": True}


class LeaveIn(BaseModel):
    from_date: date
    to_date: date
    reason: str = Field(max_length=500)


@router.post("/me/leaves", status_code=201, summary="Ask for leave")
async def ask_leave(payload: LeaveIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await _svc(db).ask_leave(await _student(db, current_user), **payload.model_dump())


