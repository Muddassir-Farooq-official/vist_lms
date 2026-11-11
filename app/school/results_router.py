"""School mode, stage 3 API: exams, marks, remarks & skills, report cards, publishing and promotion."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission
from app.school.results import ResultsService

router = APIRouter(prefix="/school", tags=["school"])
office = require_permission(Permissions.COURSE_MANAGE)


class ExamIn(BaseModel):
    kind: Literal["FIRST_TERM", "MID_TERM", "FINAL"]
    session_label: str | None = None


class TotalsIn(BaseModel):
    totals: dict[uuid.UUID, int]


class OpenIn(BaseModel):
    marks_open: bool


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


class SkillIn(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=60)
    active: bool = True


class SkillsIn(BaseModel):
    skills: list[SkillIn] = Field(min_length=1, max_length=20)


class MarkIn(BaseModel):
    student_id: uuid.UUID
    marks: float | None = None
    absent: bool = False


class MarksIn(BaseModel):
    entries: list[MarkIn] = Field(min_length=1, max_length=200)


class NoteIn(BaseModel):
    student_id: uuid.UUID
    remarks: str | None = Field(default=None, max_length=600)
    skills: dict[str, str] = Field(default_factory=dict)


class NotesIn(BaseModel):
    entries: list[NoteIn] = Field(min_length=1, max_length=200)


class PromoteItem(BaseModel):
    student_id: uuid.UUID
    decision: Literal["PROMOTED", "PROMOTED_ON_APPROVAL", "KEPT_BACK"]
    to_program_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=300)


class PromoteIn(BaseModel):
    to_session: str = Field(min_length=2, max_length=50)
    session_label: str | None = None
    items: list[PromoteItem] = Field(min_length=1, max_length=300)


# ---------------------------------------------------------------- the office
@router.get("/exams", summary="The session's exams with each class's progress (office)")
async def exams(session_label: str | None = None, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await ResultsService(db).exams(session_label)


@router.post("/exams", status_code=201, summary="Set up the 1st Term, Mid-Term or Final exam of a session (office)")
async def create_exam(payload: ExamIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await ResultsService(db).create_exam(term_label=payload.session_label, kind=payload.kind, actor_id=current_user.id)


@router.get("/exams/{exam_id}", summary="One exam with each class's progress (office)")
async def exam(exam_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await ResultsService(db).exam_view(exam_id)


@router.post("/exams/{exam_id}/marks-open", summary="Open or close marks entry for teachers (office)")
async def marks_open(exam_id: uuid.UUID, payload: OpenIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await ResultsService(db).set_open(exam_id, payload.marks_open, current_user.id)


@router.get("/exams/{exam_id}/classes/{program_id}/totals", summary="Each subject's total marks in this exam for a class (office)")
async def totals(exam_id: uuid.UUID, program_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await ResultsService(db).totals(exam_id, program_id)


@router.put("/exams/{exam_id}/classes/{program_id}/totals", summary="Change subjects' total marks, e.g. Drawing 50 (office)")
async def set_totals(exam_id: uuid.UUID, program_id: uuid.UUID, payload: TotalsIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await ResultsService(db).set_totals(exam_id, program_id, payload.totals, current_user.id)


@router.post("/exams/{exam_id}/classes/{program_id}/publish", summary="Publish a class's report cards; students and parents are told (office)")
async def publish(exam_id: uuid.UUID, program_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await ResultsService(db).publish(exam_id, program_id, current_user.id)


@router.post("/exams/{exam_id}/classes/{program_id}/withdraw", summary="Withdraw a class's published report cards, with the reason (office)")
async def withdraw(exam_id: uuid.UUID, program_id: uuid.UUID, payload: ReasonIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await ResultsService(db).withdraw(exam_id, program_id, payload.reason, current_user.id)


@router.get("/skills", summary="The skills & behaviour list on report cards")
async def skills(all: bool = False, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:  # noqa: A002
    return await ResultsService(db).skills(all_=all)


@router.put("/skills", summary="Change the skills & behaviour list (office); old ones are switched off, never deleted")
async def save_skills(payload: SkillsIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await ResultsService(db).save_skills([s.model_dump() for s in payload.skills], current_user.id)


@router.get("/promotion/{program_id}", summary="A class's students with the year's result, ready to move up (office)")
async def promotion_list(program_id: uuid.UUID, session_label: str | None = None, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await ResultsService(db).promotion_list(program_id, session_label)


@router.post("/promotion/{program_id}", summary="Move students to the next class (or keep back) in the new session (office)")
async def promote(program_id: uuid.UUID, payload: PromoteIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await ResultsService(db).promote(program_id, term_label=payload.session_label, to_term_label=payload.to_session,
                                            items=[i.model_dump() for i in payload.items], actor_id=current_user.id)


# ---------------------------------------------------------------- teachers
@router.get("/marks/mine", summary="The exams, my section-subjects, and the sections I am class teacher of (teachers)")
async def marks_mine(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await ResultsService(db).my_sheets(current_user)


@router.get("/exams/{exam_id}/marks/{offering_id}", summary="The marks sheet of one section's subject (its teacher, the office)")
async def marks_sheet(exam_id: uuid.UUID, offering_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await ResultsService(db).marks_sheet(exam_id, offering_id, current_user)


@router.put("/exams/{exam_id}/marks/{offering_id}", summary="Save marks (or Absent) for a section's subject")
async def save_marks(exam_id: uuid.UUID, offering_id: uuid.UUID, payload: MarksIn, current_user: CurrentUser = Depends(get_current_user),
                     db=Depends(get_db)) -> dict:
    return await ResultsService(db).save_marks(exam_id, offering_id, [e.model_dump() for e in payload.entries], current_user)


@router.get("/exams/{exam_id}/remarks/{section_id}", summary="Remarks and skills of a section's students (the class teacher, the office)")
async def notes_sheet(exam_id: uuid.UUID, section_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await ResultsService(db).notes_sheet(exam_id, section_id, current_user)


@router.put("/exams/{exam_id}/remarks/{section_id}", summary="Save remarks and skills ratings (A-D)")
async def save_notes(exam_id: uuid.UUID, section_id: uuid.UUID, payload: NotesIn, current_user: CurrentUser = Depends(get_current_user),
                     db=Depends(get_db)) -> dict:
    return await ResultsService(db).save_notes(exam_id, section_id, [e.model_dump() for e in payload.entries], current_user)


# ---------------------------------------------------------------- students and parents
@router.get("/students/{student_id}/report-cards", summary="A student's published report cards (the student, a parent, teachers, the office)")
async def cards(student_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await ResultsService(db).cards_for(student_id, current_user)


@router.get("/me/report-cards", summary="My published report cards (students)")
async def my_cards(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    from app.school.daily_router import _my_student_id

    return await ResultsService(db).cards_for(await _my_student_id(db, current_user), current_user)


@router.get("/students/{student_id}/report-cards/{exam_id}", summary="One report card")
async def card(student_id: uuid.UUID, exam_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await ResultsService(db).report_card(exam_id, student_id, current_user)


@router.get("/students/{student_id}/report-cards/{exam_id}/pdf", summary="One report card as a PDF")
async def card_pdf(student_id: uuid.UUID, exam_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    from app.core import brand
    from app.school.report_pdf import render_report_card

    d = await ResultsService(db).report_card(exam_id, student_id, current_user)
    pdf = render_report_card(d, institute=brand.name())
    name = f"report-card-{d['exam']['kind'].lower()}-{(d['student']['roll_number'] or 'student')}.pdf".replace(" ", "-")
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"', "Cache-Control": "no-store"})
