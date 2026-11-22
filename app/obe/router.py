"""OBE API (app/obe/service.py). Teachers work on their own classes; the exam office and administration set PLOs,
lock course files and read attainment."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission
from app.core.exceptions import AuthorizationError, ResourceNotFoundError

router = APIRouter(prefix="/obe", tags=["obe"])
office = require_permission(Permissions.RESULT_APPROVE)


def _svc(db):
    from app.obe.service import ObeService

    return ObeService(db)


async def _teacher_or_office(db, offering_id: uuid.UUID, user) -> None:
    from app.academics.models import CourseOffering
    from app.academics.router import _can_manage_marks_for_offering

    o = await db.get(CourseOffering, offering_id)
    if o is None:
        raise ResourceNotFoundError("Class not found.")
    if not await _can_manage_marks_for_offering(db, o, user):
        raise AuthorizationError("Only the class's teacher or the exam office can work on its outcomes.")


class Plo(BaseModel):
    id: uuid.UUID | None = None
    code: str = Field(min_length=1, max_length=12)
    title: str = Field(min_length=1, max_length=300)


class ProgramIn(BaseModel):
    plos: list[Plo] = Field(default_factory=list, max_length=40)
    student_pct: float = Field(default=50, gt=0, le=100)
    batch_pct: float = Field(default=60, gt=0, le=100)


@router.get("/programs", summary="Programs with how many PLOs each has")
async def programs(current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    from app.obe.models import ProgramOutcome
    from app.programs.models import Program

    counts = dict((await db.execute(select(ProgramOutcome.program_id, func.count()).group_by(ProgramOutcome.program_id))).all())
    return [{"id": p.id, "code": p.code, "name": p.name, "plos": int(counts.get(p.id, 0))}
            for p in (await db.execute(select(Program).order_by(Program.code))).scalars()]


@router.get("/programs/{program_id}", summary="A program's PLOs and pass levels")
async def program(program_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).program(program_id)


@router.put("/programs/{program_id}", summary="Save a program's PLOs and pass levels")
async def save_program(program_id: uuid.UUID, payload: ProgramIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).save_program(program_id, plos=[p.model_dump() for p in payload.plos], student_pct=payload.student_pct,
                                       batch_pct=payload.batch_pct, actor_id=current_user.id)


@router.get("/programs/{program_id}/report", summary="PLO attainment of a program (optionally only some terms)")
async def plo_report(program_id: uuid.UUID, term: list[str] | None = Query(default=None), current_user: CurrentUser = Depends(office),
                     db=Depends(get_db)) -> dict:
    return await _svc(db).plo_report(program_id, term_labels=term)


@router.get("/files", summary="Course files of open terms, with their status")
async def files(status: str | None = None, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).files(status=status)


@router.get("/my-classes", summary="My classes in open terms with their course file status (teacher)")
async def my_classes(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    from app.academics.router import _resolve_own_teacher_id

    me = await _resolve_own_teacher_id(db, current_user)
    rows = await _svc(db).files()
    if Permissions.RESULT_APPROVE in current_user.permissions:
        return rows
    if me is None:
        return []
    from app.academics.models import CourseOffering

    mine = set((await db.execute(select(CourseOffering.id).where(CourseOffering.assigned_teacher_id == me))).scalars())
    return [r for r in rows if r["offering_id"] in mine]


@router.get("/offerings/{offering_id}", summary="A class's course file: CLOs, mapping, what each assessment measures")
async def course_file(offering_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    await _teacher_or_office(db, offering_id, current_user)
    return await _svc(db).course_file(offering_id)


class Clo(BaseModel):
    id: uuid.UUID | None = None
    code: str = Field(min_length=1, max_length=12)
    text: str = Field(min_length=1, max_length=1000)
    bloom: str = Field(min_length=2, max_length=4)
    plo_ids: list[uuid.UUID] = Field(default_factory=list)


class ClosIn(BaseModel):
    clos: list[Clo] = Field(default_factory=list, max_length=20)


@router.put("/offerings/{offering_id}/clos", summary="Save the class's CLOs and their PLO mapping (while a draft)")
async def save_clos(offering_id: uuid.UUID, payload: ClosIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    await _teacher_or_office(db, offering_id, current_user)
    return await _svc(db).save_clos(offering_id, [c.model_dump() for c in payload.clos], actor_id=current_user.id)


@router.post("/offerings/{offering_id}/copy", summary="Start from the CLOs of this course's last class")
async def copy(offering_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    await _teacher_or_office(db, offering_id, current_user)
    return await _svc(db).copy_from_last(offering_id, actor_id=current_user.id)


class Item(BaseModel):
    label: str | None = Field(default=None, max_length=20)
    max_marks: float = Field(gt=0, le=1000)
    clo_id: uuid.UUID


class ItemsIn(BaseModel):
    items: list[Item] = Field(default_factory=list, max_length=40)


@router.put("/offerings/{offering_id}/assessments/{assessment_id}/items", summary="What an assessment measures: the whole of it, or question parts")
async def save_items(offering_id: uuid.UUID, assessment_id: uuid.UUID, payload: ItemsIn, current_user: CurrentUser = Depends(get_current_user),
                     db=Depends(get_db)) -> dict:
    await _teacher_or_office(db, offering_id, current_user)
    return await _svc(db).save_items(offering_id, assessment_id, [i.model_dump() for i in payload.items], actor_id=current_user.id)


@router.post("/offerings/{offering_id}/submit", summary="Send the course file to the exam office")
async def submit(offering_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    await _teacher_or_office(db, offering_id, current_user)
    return await _svc(db).submit(offering_id, actor_id=current_user.id)


class DecideIn(BaseModel):
    lock: bool
    note: str | None = Field(default=None, max_length=500)


@router.post("/offerings/{offering_id}/decide", summary="Lock a submitted course file, or send it back with a note (exam office)")
async def decide(offering_id: uuid.UUID, payload: DecideIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).decide(offering_id, lock=payload.lock, note=payload.note, actor_id=current_user.id)


@router.get("/offerings/{offering_id}/assessments/{assessment_id}/marks", summary="Question-part marks of an assessment")
async def part_marks(offering_id: uuid.UUID, assessment_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    await _teacher_or_office(db, offering_id, current_user)
    return await _svc(db).part_marks(offering_id, assessment_id)


class Mark(BaseModel):
    item_id: uuid.UUID
    enrollment_id: uuid.UUID
    obtained: float | None = None


class MarksIn(BaseModel):
    entries: list[Mark] = Field(default_factory=list, max_length=5000)


@router.put("/offerings/{offering_id}/assessments/{assessment_id}/marks", summary="Save question-part marks")
async def save_part_marks(offering_id: uuid.UUID, assessment_id: uuid.UUID, payload: MarksIn, current_user: CurrentUser = Depends(get_current_user),
                          db=Depends(get_db)) -> dict:
    await _teacher_or_office(db, offering_id, current_user)
    return await _svc(db).save_part_marks(offering_id, assessment_id, [m.model_dump() for m in payload.entries], actor_id=current_user.id)


@router.get("/offerings/{offering_id}/attainment", summary="CLO attainment of a class, per CLO and per student")
async def attainment(offering_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    await _teacher_or_office(db, offering_id, current_user)
    return await _svc(db).attainment(offering_id)
