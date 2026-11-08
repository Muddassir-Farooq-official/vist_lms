from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.audit.service import AuditService
from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission
from app.core.exceptions import AuthorizationError
from app.feedback import service as svc

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackIn(BaseModel):
    scores: dict[str, int] = Field(default_factory=dict)       # question id -> 1..5
    comment: str | None = Field(None, max_length=svc.MAX_COMMENT)


class QuestionsIn(BaseModel):
    questions: list[str] = Field(..., min_length=1, max_length=30)


async def _student_id(db, user: CurrentUser) -> uuid.UUID:
    from app.students.repository import StudentRepository

    s = await StudentRepository(db).get_by_user_id(user.id)
    if s is None:
        raise AuthorizationError("This account is not linked to a student record.")
    return s.id


async def _teacher_id(db, user: CurrentUser) -> uuid.UUID:
    from app.employees.repository import EmployeeRepository

    e = await EmployeeRepository(db).get_by_user_id(user.id)
    if e is None:
        raise AuthorizationError("This account is not linked to an employee record.")
    return e.id


@router.get("/me", summary="My courses open for feedback, coming up and done (student)")
async def my_feedback(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await svc.for_student(db, await _student_id(db, current_user))


@router.post("/me/{offering_id}", status_code=204, summary="Give feedback on one course, once (student; stored anonymously)")
async def give_feedback(offering_id: uuid.UUID, payload: FeedbackIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> None:
    await svc.submit(db, await _student_id(db, current_user), offering_id, payload.scores, payload.comment)
    await db.commit()


@router.get("/teaching", summary="Feedback on the courses I teach (teacher; after results are published)")
async def teaching_feedback(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await svc.for_teacher(db, await _teacher_id(db, current_user))


@router.get("/overview", summary="Response rates and scores for every course of a term (principal, HOD, admin)")
async def overview(term_label: str | None = None, current_user: CurrentUser = Depends(require_permission(Permissions.ACADEMIC_OVERSIGHT)),
                   db=Depends(get_db)) -> dict:
    return await svc.oversight(db, term_label)


@router.get("/overview/{offering_id}", summary="One course's scores and comments (principal, HOD, admin; after the term)")
async def overview_detail(offering_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.ACADEMIC_OVERSIGHT)),
                          db=Depends(get_db)) -> dict:
    return await svc.oversight_detail(db, offering_id)


@router.get("/questions", summary="The survey questions")
async def get_questions(current_user: CurrentUser = Depends(require_permission(Permissions.ACADEMIC_OVERSIGHT)), db=Depends(get_db)) -> dict:
    qs = await svc.questions(db)
    await db.commit()
    return {"questions": [q.text for q in qs], "scale": svc.SCALE, "window_days": svc.WINDOW_DAYS, "min_responses": svc.MIN_RESPONSES}


@router.put("/questions", summary="Change the survey questions (admin)")
async def put_questions(payload: QuestionsIn, current_user: CurrentUser = Depends(require_permission(Permissions.POLICY_MANAGE)), db=Depends(get_db)) -> dict:
    qs = await svc.set_questions(db, payload.questions)
    await AuditService(db).log(actor_user_id=current_user.id, action="feedback.questions_changed", entity_type="feedback_questions",
                               entity_id=None, after={"questions": [q.text for q in qs]})
    await db.commit()
    return {"questions": [q.text for q in qs]}
