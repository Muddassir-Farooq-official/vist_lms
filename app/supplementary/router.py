"""Supplementary (reappear) exams. Exam office: register failed students, enter marks, publish. Students: see their own."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_permission
from app.core.exceptions import AuthorizationError
from app.supplementary import models as _models  # noqa: F401  (registers the table)
from app.supplementary.service import SupplementaryService

router = APIRouter(prefix="/supplementary", tags=["supplementary"])
manage = require_permission(Permissions.EXAM_SCHEDULE_MANAGE)


class RegisterIn(BaseModel):
    enrollment_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    offering_assessment_id: uuid.UUID | None = None   # which component the paper replaces (default: the final exam)


class MarkIn(BaseModel):
    obtained_marks: float | None = Field(default=None, ge=0)
    absent: bool = False


class PublishIn(BaseModel):
    attempt_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


@router.get("/eligible", summary="Students who failed a course on marks in this term and can sit a supplementary (exam office)")
async def eligible(term_label: str, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    return await SupplementaryService(db).eligible(term_label)


@router.get("/attempts", summary="Registered supplementary attempts for a term, with their status (exam office)")
async def attempts(term_label: str, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    return await SupplementaryService(db).listing(term_label)


@router.post("/register", status_code=201, summary="Register failed students for a supplementary (exam office)")
async def register(payload: RegisterIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    done = await SupplementaryService(db).register(payload.enrollment_ids, actor_id=current_user.id, offering_assessment_id=payload.offering_assessment_id)
    return {"registered": len(done)}


@router.post("/attempts/{attempt_id}/mark", summary="Enter the supplementary paper's mark, or mark the student absent (exam office)")
async def mark(attempt_id: uuid.UUID, payload: MarkIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    svc = SupplementaryService(db)
    a = await svc.enter_mark(attempt_id, obtained=payload.obtained_marks, absent=payload.absent, actor_id=current_user.id)
    return {"id": a.id, "status": a.status.value, "preview": await svc.preview(a)}


@router.delete("/attempts/{attempt_id}", status_code=204, summary="Cancel a registration that is not yet published (exam office)")
async def cancel(attempt_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> Response:
    await SupplementaryService(db).cancel(attempt_id)
    return Response(status_code=204)


@router.post("/publish", summary="Publish supplementary results: a pass replaces the failed result; students are notified (exam office)")
async def publish(payload: PublishIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SupplementaryService(db).publish(payload.attempt_ids, actor_id=current_user.id)


@router.get("/me", summary="My supplementary attempts (student); marks show once published")
async def mine(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    from app.students.models import Student

    sid = (await db.execute(select(Student.id).where(Student.user_id == current_user.id))).scalar_one_or_none()
    if sid is None:
        raise AuthorizationError("Only students have supplementary attempts.")
    return await SupplementaryService(db).listing(student_id=sid)