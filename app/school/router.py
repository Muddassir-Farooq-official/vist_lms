"""School mode API: /api/v1/school/... (classes, sections, subjects, teachers, placing students)."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission
from app.core.exceptions import AuthorizationError
from app.school.service import SchoolService

router = APIRouter(prefix="/school", tags=["school"])
manage = require_permission(Permissions.COURSE_MANAGE)
place = require_permission(Permissions.ENROLLMENT_MANAGE)


class SetupIn(BaseModel):
    term_label: str = Field(min_length=2, max_length=50)
    start_date: date | None = None
    end_date: date | None = None
    groups: list[str] = Field(min_length=1, max_length=20)
    sections: list[str] = Field(default_factory=lambda: ["A"], max_length=12)


class SectionIn(BaseModel):
    label: str = Field(min_length=1, max_length=30)
    term_label: str | None = None


class SectionPatch(BaseModel):
    class_teacher_id: uuid.UUID | None = None
    room: str | None = Field(default=None, max_length=50)
    capacity: int | None = None


class SubjectIn(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    code: str | None = Field(default=None, max_length=8)
    optional: bool = False
    term_label: str | None = None


class TeachersIn(BaseModel):
    teachers: dict[uuid.UUID, uuid.UUID | None]


class PlaceIn(BaseModel):
    student_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    optional_course_ids: list[uuid.UUID] = Field(default_factory=list)
    charge_admission: bool = False      # new admissions: charge the class's admission fee (School fees), once per student


@router.get("/overview", summary="Classes, sections, class teachers and subjects for a session (admin)")
async def overview(session_label: str | None = None, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SchoolService(db).overview(session_label)


@router.get("/teachers", summary="Teachers who can be class or subject teachers")
async def teachers(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    return await SchoolService(db).teachers()


@router.post("/setup", summary="Set up classes, subjects and sections for a session; safe to run again (admin, School mode)")
async def setup(payload: SetupIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SchoolService(db).setup(term_label=payload.term_label, start_date=payload.start_date, end_date=payload.end_date,
                                         groups=payload.groups, sections=payload.sections, actor_id=current_user.id)


@router.post("/classes/{program_id}/sections", status_code=201, summary="Add a section to a class")
async def add_section(program_id: uuid.UUID, payload: SectionIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SchoolService(db).add_section(program_id, term_label=payload.term_label, label=payload.label, actor_id=current_user.id)


@router.post("/classes/{program_id}/subjects", status_code=201, summary="Add a subject to a class")
async def add_subject(program_id: uuid.UUID, payload: SubjectIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SchoolService(db).add_subject(program_id, title=payload.title, code=payload.code, optional=payload.optional,
                                               term_label=payload.term_label, actor_id=current_user.id)


@router.delete("/classes/{program_id}/subjects/{course_id}", status_code=204, summary="Take a subject off a class (only before anyone is enrolled)")
async def remove_subject(program_id: uuid.UUID, course_id: uuid.UUID, session_label: str | None = None,
                         current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> Response:
    await SchoolService(db).remove_subject(program_id, course_id, term_label=session_label, actor_id=current_user.id)
    return Response(status_code=204)


@router.get("/sections/{section_id}", summary="A section: class teacher, subjects with their teachers, students")
async def section(section_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SchoolService(db).section_detail(section_id)


@router.patch("/sections/{section_id}", summary="Set the class teacher, room or seats of a section")
async def update_section(section_id: uuid.UUID, payload: SectionPatch, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SchoolService(db).update_section(section_id, changes=payload.model_dump(exclude_unset=True), actor_id=current_user.id)


@router.delete("/sections/{section_id}", status_code=204, summary="Remove an empty section")
async def remove_section(section_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> Response:
    await SchoolService(db).remove_section(section_id, actor_id=current_user.id)
    return Response(status_code=204)


@router.put("/sections/{section_id}/teachers", summary="Set the subject teachers of a section ({course_id: employee_id or null})")
async def set_teachers(section_id: uuid.UUID, payload: TeachersIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SchoolService(db).set_subject_teachers(section_id, payload.teachers, actor_id=current_user.id)


@router.get("/sections/{section_id}/candidates", summary="Students who can be put in this section (search by name or roll number)")
async def candidates(section_id: uuid.UUID, q: str | None = None, current_user: CurrentUser = Depends(place), db=Depends(get_db)) -> list[dict]:
    return await SchoolService(db).candidates(section_id, q)


@router.post("/sections/{section_id}/students", summary="Put students in this section and enrol them in its subjects")
async def place_students(section_id: uuid.UUID, payload: PlaceIn, current_user: CurrentUser = Depends(place), db=Depends(get_db)) -> dict:
    if payload.charge_admission and Permissions.FEE_MANAGE not in current_user.permissions:
        raise AuthorizationError("Only the administration can charge the admission fee.")
    res = await SchoolService(db).place(section_id, payload.student_ids, optional_course_ids=payload.optional_course_ids, actor_id=current_user.id)
    if payload.charge_admission:
        from app.school.fees import SchoolFeeService

        fees = SchoolFeeService(db)
        charged = 0
        for p in res["placed"]:
            if await fees.charge_admission(p["student_id"], current_user.id) is not None:
                charged += 1
        res["admission_vouchers"] = charged
    return res
