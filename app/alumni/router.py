"""Graduation, degrees and transcripts, and alumni API (app/alumni/service.py)."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission

router = APIRouter(prefix="/alumni", tags=["alumni"])
office = require_permission(Permissions.ALUMNI_MANAGE)
documents_desk = require_permission(Permissions.DOCUMENT_ISSUE)
alumnus = require_permission(Permissions.ALUMNI_SELF)


def _svc(db):
    from app.alumni.service import AlumniService

    return AlumniService(db)


# ---------------------------------------------------------------- graduation lists (administration)
class ListIn(BaseModel):
    program_id: uuid.UUID
    title: str = Field(max_length=120)
    graduation_date: date


@router.post("/graduation", status_code=201, summary="Draw up a graduation list: every current student of the program, checked")
async def draw_up(payload: ListIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).draw_up(**payload.model_dump(), actor_id=current_user.id)


@router.get("/graduation", summary="Graduation lists")
async def lists(current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).lists()


@router.get("/graduation/{list_id}", summary="A graduation list with each student's checks")
async def list_detail(list_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).list_detail(list_id)


class IncludeIn(BaseModel):
    included: bool
    note: str | None = Field(default=None, max_length=300)


@router.post("/graduation/{list_id}/entries/{entry_id}", summary="Tick or untick a student (a student not clear needs a reason)")
async def include(list_id: uuid.UUID, entry_id: uuid.UUID, payload: IncludeIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).set_included(list_id, entry_id, included=payload.included, note=payload.note)


@router.post("/graduation/{list_id}/refresh", summary="Check every student again")
async def refresh(list_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).refresh(list_id)


@router.post("/graduation/{list_id}/confirm", summary="Graduate every ticked student and give them an alumni login")
async def confirm(list_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).confirm(list_id, actor_id=current_user.id)


@router.delete("/graduation/{list_id}", summary="Delete a list not yet confirmed")
async def delete_list(list_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    await _svc(db).delete_draft(list_id)
    return {"deleted": True}


@router.get("/graduation/{list_id}/pdf", summary="The graduation list as a PDF")
async def list_pdf(list_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> Response:
    body = await _svc(db).list_pdf(list_id)
    return Response(content=body, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="graduation-list.pdf"', "Cache-Control": "no-store"})


@router.post("/students/{student_id}/login", summary="Give an earlier graduate an alumni login (their old login, switched back on)")
async def alumni_login(student_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return {"has_login": await _svc(db).make_alumnus(student_id)}


@router.get("/directory/all", summary="Every graduate with their details (administration)")
async def directory_all(q: str | None = None, program_id: uuid.UUID | None = None, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).directory(q=q, program_id=program_id, everyone=True)


@router.get("/directory/all.csv", summary="Every graduate, as a spreadsheet")
async def directory_csv(program_id: uuid.UUID | None = None, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> Response:
    svc = _svc(db)
    body = svc.directory_csv(await svc.directory(program_id=program_id, everyone=True))
    return Response(content=body, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="alumni.csv"', "Cache-Control": "no-store"})


class SurveyIn(BaseModel):
    title: str = Field(max_length=150)
    intro: str | None = Field(default=None, max_length=2000)
    questions: list[dict] | None = Field(default=None, max_length=40)
    is_open: bool | None = None


@router.get("/surveys", summary="Alumni surveys with how many answered")
async def surveys(current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).surveys()


@router.post("/surveys", status_code=201, summary="Make a survey (open it to tell every alumnus)")
async def add_survey(payload: SurveyIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).save_survey(payload.model_dump(exclude_none=True), survey_id=None, actor_id=current_user.id)


@router.put("/surveys/{survey_id}", summary="Change a survey, open or close it")
async def edit_survey(survey_id: uuid.UUID, payload: SurveyIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).save_survey(payload.model_dump(exclude_none=True), survey_id=survey_id, actor_id=current_user.id)


@router.get("/surveys/{survey_id}/results", summary="A survey's answers, totalled")
async def survey_results(survey_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).results(survey_id)


# ---------------------------------------------------------------- degrees and transcripts (administration, exam office)
@router.get("/documents", summary="Degrees and transcripts being prepared or handed over")
async def documents(status: str | None = "OPEN", q: str | None = None, current_user: CurrentUser = Depends(documents_desk), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).documents(status=status or None, q=q)


class DocIn(BaseModel):
    student_id: uuid.UUID | None = None
    roll_number: str | None = Field(default=None, max_length=30)
    kind: str = Field(pattern="^(DEGREE|TRANSCRIPT)$")
    copies: int = Field(default=1, ge=1, le=10)
    note: str | None = Field(default=None, max_length=300)


@router.post("/documents", status_code=201, summary="Start a degree or transcript for a student")
async def open_document(payload: DocIn, current_user: CurrentUser = Depends(documents_desk), db=Depends(get_db)) -> dict:
    from sqlalchemy import select

    from app.core.exceptions import ResourceNotFoundError
    from app.students.models import Student

    sid = payload.student_id
    if sid is None and payload.roll_number:
        sid = (await db.execute(select(Student.id).where(Student.roll_number == payload.roll_number.strip()))).scalar_one_or_none()
    if sid is None:
        raise ResourceNotFoundError("Student not found.")
    return await _svc(db).open_document(student_id=sid, kind=payload.kind, copies=payload.copies, note=payload.note, actor_id=current_user.id)


class AdvanceIn(BaseModel):
    status: str = Field(pattern="^(PREPARING|READY|COLLECTED|POSTED|CANCELLED)$")
    serial_no: str | None = Field(default=None, max_length=40)
    handed_to: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=300)


@router.post("/documents/{doc_id}", summary="Move a degree or transcript on (being prepared, ready, collected or posted)")
async def advance(doc_id: uuid.UUID, payload: AdvanceIn, current_user: CurrentUser = Depends(documents_desk), db=Depends(get_db)) -> dict:
    return await _svc(db).advance(doc_id, **payload.model_dump(), actor_id=current_user.id)


# ---------------------------------------------------------------- the alumni portal
@router.get("/me", summary="My alumni details, degree and transcripts")
async def me(current_user: CurrentUser = Depends(alumnus), db=Depends(get_db)) -> dict:
    svc = _svc(db)
    return await svc.me(await svc.assert_alumnus(current_user))


class ProfileIn(BaseModel):
    employer: str | None = Field(default=None, max_length=150)
    job_title: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=80)
    country: str | None = Field(default=None, max_length=80)
    further_study: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=150)
    phone: str | None = Field(default=None, max_length=30)
    linkedin: str | None = Field(default=None, max_length=200)
    in_directory: bool = False
    share_contact: bool = False


@router.put("/me", summary="Update my work and contact details, and whether I am in the directory")
async def save_me(payload: ProfileIn, current_user: CurrentUser = Depends(alumnus), db=Depends(get_db)) -> dict:
    svc = _svc(db)
    return await svc.save_profile(await svc.assert_alumnus(current_user), payload.model_dump())


@router.get("/directory", summary="Alumni who chose to be listed")
async def directory(q: str | None = None, current_user: CurrentUser = Depends(alumnus), db=Depends(get_db)) -> list[dict]:
    svc = _svc(db)
    await svc.assert_alumnus(current_user)
    return await svc.directory(q=q)


@router.get("/me/surveys", summary="Open surveys, and whether I answered")
async def my_surveys(current_user: CurrentUser = Depends(alumnus), db=Depends(get_db)) -> list[dict]:
    svc = _svc(db)
    s = await svc.assert_alumnus(current_user)
    return await svc.surveys(open_only=True, student_id=s.id)


class AnswerIn(BaseModel):
    answers: dict


@router.post("/me/surveys/{survey_id}", summary="Answer a survey (once)")
async def answer(survey_id: uuid.UUID, payload: AnswerIn, current_user: CurrentUser = Depends(alumnus), db=Depends(get_db)) -> dict:
    svc = _svc(db)
    return await svc.answer(await svc.assert_alumnus(current_user), survey_id, payload.answers)


class AskIn(BaseModel):
    copies: int = Field(default=1, ge=1, le=10)
    note: str | None = Field(default=None, max_length=300)


@router.post("/me/transcript", status_code=201, summary="Ask the office for a printed transcript")
async def ask_transcript(payload: AskIn, current_user: CurrentUser = Depends(alumnus), db=Depends(get_db)) -> dict:
    svc = _svc(db)
    s = await svc.assert_alumnus(current_user)
    return await svc.open_document(student_id=s.id, kind="TRANSCRIPT", copies=payload.copies, note=payload.note, actor_id=current_user.id, by_student=True)

