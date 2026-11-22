"""Question bank API (app/qbank/service.py). Teachers: their courses' questions. Exam office: every course, and papers.
There is no student endpoint."""
from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_any_permission, require_permission

router = APIRouter(prefix="/qbank", tags=["question bank"])
writer = require_any_permission(Permissions.MARKS_ENTER, Permissions.EXAM_SCHEDULE_MANAGE)      # teachers and the exam office
office = require_permission(Permissions.EXAM_SCHEDULE_MANAGE)


def _svc(db):
    from app.qbank.service import QuestionBankService

    return QuestionBankService(db)


def _office(user: CurrentUser) -> bool:
    return Permissions.EXAM_SCHEDULE_MANAGE in user.permissions


@router.get("/courses", summary="Courses I can add questions to, with how many each has")
async def courses(current_user: CurrentUser = Depends(writer), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).courses(current_user, office=_office(current_user))


@router.get("/courses/{course_id}/questions", summary="A course's questions (with answers: never shown to students)")
async def questions(course_id: uuid.UUID, kind: str | None = None, include_retired: bool = False, current_user: CurrentUser = Depends(writer),
                    db=Depends(get_db)) -> dict:
    return await _svc(db).questions(course_id, user=current_user, office=_office(current_user), kind=kind, include_retired=include_retired)


class QuestionIn(BaseModel):
    course_id: uuid.UUID | None = None
    kind: str | None = Field(default=None, pattern="^(MCQ|SHORT|LONG)$")
    text: str | None = Field(default=None, max_length=5000)
    options: list[str] | None = Field(default=None, max_length=6)
    answer: str | None = Field(default=None, max_length=3000)
    clo: str | None = Field(default=None, max_length=12)
    bloom: str | None = Field(default=None, max_length=4)
    difficulty: str | None = Field(default=None, pattern="^(EASY|MEDIUM|HARD)$")
    marks: Decimal | None = Field(default=None, ge=0, le=100)
    is_active: bool | None = None


@router.post("/questions", status_code=201, summary="Add a question")
async def add(payload: QuestionIn, current_user: CurrentUser = Depends(writer), db=Depends(get_db)) -> dict:
    return await _svc(db).add(payload.model_dump(), user=current_user, office=_office(current_user))


@router.put("/questions/{question_id}", summary="Change a question (or retire it: is_active false)")
async def edit(question_id: uuid.UUID, payload: QuestionIn, current_user: CurrentUser = Depends(writer), db=Depends(get_db)) -> dict:
    return await _svc(db).edit(question_id, payload.model_dump(exclude_none=True, exclude={"course_id"}), user=current_user, office=_office(current_user))


# ---------------------------------------------------------------- papers (exam office)
class BlueprintRow(BaseModel):
    kind: str = Field(pattern="^(MCQ|SHORT|LONG)$")
    count: int = Field(ge=1, le=100)
    difficulty: str | None = Field(default=None, pattern="^(EASY|MEDIUM|HARD)$")
    bloom: str | None = Field(default=None, max_length=4)
    clo: str | None = Field(default=None, max_length=12)
    marks: Decimal | None = Field(default=None, gt=0, le=100)


class BuildIn(BaseModel):
    course_id: uuid.UUID
    title: str = Field(max_length=150)
    blueprint: list[BlueprintRow] = Field(min_length=1, max_length=20)
    exam_paper_id: uuid.UUID | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=600)
    instructions: str | None = Field(default=None, max_length=1000)


@router.post("/papers", status_code=201, summary="Build a paper from a blueprint")
async def build(payload: BuildIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    data = payload.model_dump()
    return await _svc(db).build(**data, actor_id=current_user.id)


@router.get("/papers", summary="Papers, newest first")
async def papers(course_id: uuid.UUID | None = None, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).papers(course_id=course_id)


@router.get("/papers/{paper_id}", summary="A paper with its questions, and marks by CLO and Bloom level")
async def paper(paper_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).paper(paper_id)


@router.post("/papers/{paper_id}/items/{item_id}/swap", summary="Swap a question for another like it")
async def swap(paper_id: uuid.UUID, item_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).swap(paper_id, item_id)


class MoveIn(BaseModel):
    step: int = Field(ge=-1, le=1)


@router.post("/papers/{paper_id}/items/{item_id}/move", summary="Move a question up (-1) or down (1)")
async def move(paper_id: uuid.UUID, item_id: uuid.UUID, payload: MoveIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).move(paper_id, item_id, payload.step)


@router.delete("/papers/{paper_id}/items/{item_id}", summary="Take a question out")
async def remove_item(paper_id: uuid.UUID, item_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).remove_item(paper_id, item_id)


class AddItemIn(BaseModel):
    question_id: uuid.UUID
    marks: Decimal | None = Field(default=None, gt=0, le=100)


@router.post("/papers/{paper_id}/items", summary="Add a chosen question")
async def add_item(paper_id: uuid.UUID, payload: AddItemIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).add_item(paper_id, payload.question_id, payload.marks)


@router.post("/papers/{paper_id}/final", summary="Make the paper final (it can no longer change)")
async def finalise(paper_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).finalise(paper_id, actor_id=current_user.id)


@router.delete("/papers/{paper_id}", summary="Delete a draft paper")
async def delete(paper_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    await _svc(db).delete_draft(paper_id)
    return {"deleted": True}


@router.get("/papers/{paper_id}/pdf", summary="The question paper (key=true: the answer key)")
async def pdf(paper_id: uuid.UUID, key: bool = False, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> Response:
    from app.qbank.service import paper_pdf

    p = await _svc(db).paper(paper_id)
    body = await run_in_threadpool(paper_pdf, p, key=key)
    name = ("key-" if key else "paper-") + str(paper_id)[:8] + ".pdf"
    return Response(content=body, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
