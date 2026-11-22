"""Substitute teachers API (app/substitutes/service.py)."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission
from app.core.exceptions import AuthorizationError

router = APIRouter(prefix="/substitutes", tags=["substitutes"])
office = require_permission(Permissions.ATTENDANCE_LOCK)        # the administration


def _svc(db):
    from app.substitutes.service import SubstituteService

    return SubstituteService(db)


async def _me(db, user) -> uuid.UUID | None:
    from app.employees.models import Employee

    return (await db.execute(select(Employee.id).where(Employee.user_id == user.id))).scalar_one_or_none()


@router.get("", summary="Classes whose teacher is on leave, with the proposed or confirmed substitute (administration)")
async def listing(status: str | None = None, from_day: date | None = None, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).listing(status=status, from_day=from_day)


@router.get("/{class_session_id}/candidates", summary="Teachers who could take this class (free ones first)")
async def candidates(class_session_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).candidates(class_session_id)


class DecideIn(BaseModel):
    action: str = Field(pattern="^(CONFIRM|CANCEL)$")
    employee_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=300)


@router.post("/{class_session_id}", summary="Confirm a substitute (the proposed one or another), or cancel the class")
async def decide(class_session_id: uuid.UUID, payload: DecideIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).decide(class_session_id, action=payload.action, employee_id=payload.employee_id, note=payload.note, actor_id=current_user.id)


@router.get("/mine", summary="Classes I take as a substitute (teacher)")
async def mine(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    me = await _me(db, current_user)
    return await _svc(db).mine(me) if me else []


@router.get("/mine/{class_session_id}/students", summary="The students of a class I take as a substitute, to mark attendance")
async def students(class_session_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    from app.academics.models import ClassSession, Enrollment, EnrollmentStatus
    from app.attendance.models import AttendanceRecord
    from app.students.models import Student

    if not await _svc(db).is_substitute(class_session_id, await _me(db, current_user)):
        raise AuthorizationError("You are not the substitute for this class.")
    cs = await db.get(ClassSession, class_session_id)
    rows = (await db.execute(select(Student).join(Enrollment, Enrollment.student_id == Student.id).where(
        Enrollment.course_offering_id == cs.course_offering_id, Enrollment.status == EnrollmentStatus.ENROLLED).order_by(Student.roll_number))).scalars().all()
    marks = {r.student_id: r.status.value for r in (await db.execute(select(AttendanceRecord).where(AttendanceRecord.class_session_id == class_session_id))).scalars()}
    return {"class_session_id": cs.id, "students": [{"id": s.id, "roll_number": s.roll_number, "name": f"{s.first_name} {s.last_name}", "status": marks.get(s.id)}
                                                     for s in rows]}
