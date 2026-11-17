"""Self-assessment API (marks of paper quizzes and tests; never part of results)."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError
from app.selfassess.service import KINDS, SelfAssessmentService

router = APIRouter(prefix="/self-assessments", tags=["self-assessments"])


class AssessmentIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    kind: str = Field(default="QUIZ", pattern="^(QUIZ|TEST|ASSIGNMENT|PRESENTATION|OTHER)$")
    held_on: date
    max_marks: float = Field(gt=0, le=1000)
    topic: str | None = Field(default=None, max_length=2000)


class MarkIn(BaseModel):
    enrollment_id: uuid.UUID
    obtained: float | None = Field(default=None, ge=0)
    absent: bool = False
    remark: str | None = Field(default=None, max_length=200)


class MarksIn(BaseModel):
    entries: list[MarkIn] = Field(max_length=2000)


def _out(a) -> dict:
    return {"id": a.id, "course_offering_id": a.course_offering_id, "title": a.title, "kind": a.kind, "kind_label": KINDS.get(a.kind, a.kind),
            "held_on": a.held_on, "max_marks": float(a.max_marks), "topic": a.topic}


@router.get("/kinds")
async def kinds(current_user: CurrentUser = Depends(get_current_user)) -> dict:
    return KINDS


@router.get("/my-courses", summary="Courses I teach (for the self-assessment page)")
async def my_courses(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await SelfAssessmentService(db).my_courses(current_user)


@router.get("/courses/{offering_id}", summary="A course's self-assessments with every student's marks (its teacher or an administrator)")
async def course_view(offering_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await SelfAssessmentService(db).course_view(offering_id, current_user)


@router.post("/courses/{offering_id}", status_code=201, summary="Add a self-assessment (done on paper) to a course")
async def create(offering_id: uuid.UUID, payload: AssessmentIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    a = await SelfAssessmentService(db).create(offering_id, title=payload.title, kind=payload.kind, held_on=payload.held_on,
                                               max_marks=payload.max_marks, topic=payload.topic, user=current_user)
    return _out(a)


@router.put("/{assessment_id}", summary="Change a self-assessment's title, kind, date, total or topic")
async def update(assessment_id: uuid.UUID, payload: AssessmentIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    a = await SelfAssessmentService(db).update(assessment_id, title=payload.title, kind=payload.kind, held_on=payload.held_on,
                                               max_marks=payload.max_marks, topic=payload.topic, user=current_user)
    return _out(a)


@router.delete("/{assessment_id}", status_code=204, summary="Delete a self-assessment and its marks")
async def delete(assessment_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> None:
    await SelfAssessmentService(db).delete(assessment_id, user=current_user)


@router.put("/{assessment_id}/marks", summary="Enter or change marks (empty = no mark; absent)")
async def save_marks(assessment_id: uuid.UUID, payload: MarksIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await SelfAssessmentService(db).save_marks(assessment_id, [e.model_dump() for e in payload.entries], user=current_user)


@router.post("/{assessment_id}/marks.csv", summary="Upload marks from a CSV (roll_number, marks; A = absent)")
async def upload_marks(assessment_id: uuid.UUID, file: UploadFile = File(...), current_user: CurrentUser = Depends(get_current_user),
                       db=Depends(get_db)) -> dict:
    raw = await file.read(2_000_001)
    if len(raw) > 2_000_000:
        raise BusinessRuleViolationError("The file is too big (2 MB at most).")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return await SelfAssessmentService(db).upload_csv(assessment_id, text, user=current_user)


@router.get("/me", summary="My self-assessment marks (student)")
async def mine(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    from app.students.repository import StudentRepository

    st = await StudentRepository(db).get_by_user_id(current_user.id)
    if st is None:
        raise AuthorizationError("This account is not linked to a student record.")
    return await SelfAssessmentService(db).for_student(st.id)


@router.get("/children/{student_id}", summary="A child's self-assessment marks (parent)")
async def child(student_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    from app.exams.router import _child

    return await SelfAssessmentService(db).for_student(await _child(db, current_user, student_id))
