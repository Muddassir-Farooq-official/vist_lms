"""Exam datesheets and admit cards. Exam office (admin + exam controller) builds and publishes;
students see their own datesheet and download their admit card; teachers see their papers;
parents see their child's datesheet and admit card."""
from __future__ import annotations

import uuid
from datetime import date, time
from typing import Literal

from fastapi import APIRouter, Depends, Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.permissions import Permissions, Roles
from app.auth.schemas import CurrentUser
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_permission
from app.core.exceptions import AuthorizationError, ResourceNotFoundError
from app.exams import models as _models  # noqa: F401  (registers the tables)
from app.exams.duty import ExamDutyService
from app.exams.service import ExamScheduleService

router = APIRouter(prefix="/exams", tags=["exams"])
manage = require_permission(Permissions.EXAM_SCHEDULE_MANAGE)


class ScheduleIn(BaseModel):
    term_label: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=80)
    instructions: str | None = Field(default=None, max_length=1000)
    assessment_type_id: uuid.UUID | None = None      # the result type these papers' marks go into (optional)


class PaperIn(BaseModel):
    course_offering_id: uuid.UUID
    exam_date: date
    start_time: time
    end_time: time
    room: str = Field(min_length=1, max_length=50)
    invigilator_employee_id: uuid.UUID | None = None


class PaperPatch(BaseModel):
    exam_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    room: str | None = Field(default=None, max_length=50)
    invigilator_employee_id: uuid.UUID | None = None


class DutyIn(BaseModel):
    employee_ids: list[uuid.UUID] = Field(default_factory=list, max_length=30)
    in_charge_id: uuid.UUID | None = None


class RollEntry(BaseModel):
    student_id: uuid.UUID
    status: Literal["PRESENT", "ABSENT", "LATE"]


class RollIn(BaseModel):
    entries: list[RollEntry] = Field(min_length=1, max_length=1000)


class CaseIn(BaseModel):
    student_id: uuid.UUID
    note: str = Field(min_length=1, max_length=1000)


class DecisionIn(BaseModel):
    decision: str = Field(min_length=1, max_length=1000)


async def _pdf(card: dict) -> Response:
    from app.exams.admit_card_pdf import render_admit_card_pdf

    name = f"admit-card-{card['student']['roll_number']}-{card['schedule']['name']}".replace(" ", "-")
    return Response(content=await run_in_threadpool(render_admit_card_pdf, card), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}.pdf"', "Cache-Control": "no-store"})


# ------------------------------------------------------------------ exam office
@router.get("/schedules", summary="All datesheets (exam office)")
async def list_schedules(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    return [ExamScheduleService.summary(s) for s in await ExamScheduleService(db).list()]


@router.post("/schedules", status_code=201, summary="Start a datesheet for a term (exam office)")
async def create_schedule(payload: ScheduleIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    s = await ExamScheduleService(db).create(term_label=payload.term_label, name=payload.name, instructions=payload.instructions, actor_id=current_user.id,
                                             assessment_type_id=payload.assessment_type_id)
    return ExamScheduleService.summary(s)


@router.get("/schedules/{schedule_id}", summary="One datesheet with its papers and any clashes (exam office)")
async def get_schedule(schedule_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await ExamScheduleService(db).detail(schedule_id)


@router.post("/schedules/{schedule_id}/papers", status_code=201, summary="Add a paper (exam office)")
async def add_paper(schedule_id: uuid.UUID, payload: PaperIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    svc = ExamScheduleService(db)
    await svc.add_paper(schedule_id, **payload.model_dump(), actor_id=current_user.id)
    return await svc.detail(schedule_id)


@router.patch("/papers/{paper_id}", summary="Change a paper's date, time, room or invigilator (exam office)")
async def update_paper(paper_id: uuid.UUID, payload: PaperPatch, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    svc = ExamScheduleService(db)
    paper = await svc.update_paper(paper_id, actor_id=current_user.id, **payload.model_dump(exclude_unset=True))
    return await svc.detail(paper.schedule_id)


@router.delete("/papers/{paper_id}", status_code=204, summary="Remove a paper (exam office)")
async def delete_paper(paper_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> Response:
    await ExamScheduleService(db).delete_paper(paper_id)
    return Response(status_code=204)


@router.post("/schedules/{schedule_id}/publish", summary="Publish the datesheet (refused while any clash exists); students are notified")
async def publish(schedule_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    s = await ExamScheduleService(db).publish(schedule_id, actor_id=current_user.id)
    return ExamScheduleService.summary(s)


@router.get("/papers/{paper_id}/duty", summary="Who is on duty for a paper, how many are needed, and every teacher with whether they are free (exam office)")
async def duty_plan(paper_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await ExamDutyService(db).plan(paper_id)


@router.put("/papers/{paper_id}/duty", summary="Set the teachers on duty for a paper, one in charge (exam office); they are told once it is published")
async def set_duty(paper_id: uuid.UUID, payload: DutyIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await ExamDutyService(db).assign(paper_id, employee_ids=payload.employee_ids, in_charge_id=payload.in_charge_id, actor_id=current_user.id)


@router.get("/cases", summary="Unfair means cases (exam office)")
async def list_cases(status: Literal["OPEN", "DECIDED"] | None = None, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    return await ExamDutyService(db).cases(status=status)


@router.post("/cases/{case_id}/decide", summary="Record the decision on an unfair means case (exam office)")
async def decide_case(case_id: uuid.UUID, payload: DecisionIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await ExamDutyService(db).decide(case_id, payload.decision, current_user.id)


@router.get("/schedules/{schedule_id}/admit-cards/{student_id}/pdf", summary="Print any student's admit card (exam office)")
async def staff_admit_card(schedule_id: uuid.UUID, student_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> Response:
    return await _pdf(await ExamScheduleService(db).admit_card(schedule_id, student_id))


# ------------------------------------------------------------------ students, parents, teachers
async def _own_student(db, user: CurrentUser) -> uuid.UUID:
    from app.students.models import Student

    sid = (await db.execute(select(Student.id).where(Student.user_id == user.id))).scalar_one_or_none()
    if sid is None:
        raise AuthorizationError("Only students have a datesheet here.")
    return sid


async def _child(db, user: CurrentUser, student_id: uuid.UUID) -> uuid.UUID:
    from app.parents.models import ParentStudentLink

    if Roles.PARENT not in user.roles or await db.get(ParentStudentLink, (user.id, student_id)) is None:
        raise ResourceNotFoundError("Student not found.")
    return student_id


@router.get("/me/datesheets", summary="My published datesheets (student)")
async def my_datesheets(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await ExamScheduleService(db).for_student(await _own_student(db, current_user))


@router.get("/me/admit-card/{schedule_id}", summary="My admit card details (student): eligibility per paper and any hold")
async def my_admit_card(schedule_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await ExamScheduleService(db).admit_card(schedule_id, await _own_student(db, current_user))


@router.get("/me/admit-card/{schedule_id}/pdf", summary="Download my admit card (student)")
async def my_admit_card_pdf(schedule_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    return await _pdf(await ExamScheduleService(db).admit_card(schedule_id, await _own_student(db, current_user)))


@router.get("/children/{student_id}/datesheets", summary="A linked child's published datesheets (parent)")
async def child_datesheets(student_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await ExamScheduleService(db).for_student(await _child(db, current_user, student_id))


@router.get("/children/{student_id}/admit-card/{schedule_id}/pdf", summary="A linked child's admit card (parent)")
async def child_admit_card_pdf(student_id: uuid.UUID, schedule_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user),
                               db=Depends(get_db)) -> Response:
    return await _pdf(await ExamScheduleService(db).admit_card(schedule_id, await _child(db, current_user, student_id)))


@router.get("/teaching/papers", summary="Published papers of the courses I teach or invigilate (staff)")
async def my_papers(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    from app.employees.models import Employee

    emp = (await db.execute(select(Employee.id).where(Employee.user_id == current_user.id))).scalar_one_or_none()
    if emp is None:
        raise AuthorizationError("Only staff have exam duties here.")
    return await ExamScheduleService(db).for_teacher(emp)

@router.get("/teaching/duties", summary="My exam duties (staff)")
async def my_duties(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    from app.employees.models import Employee

    emp = (await db.execute(select(Employee.id).where(Employee.user_id == current_user.id))).scalar_one_or_none()
    if emp is None:
        raise AuthorizationError("Only staff have exam duties here.")
    return await ExamDutyService(db).mine(emp)


@router.get("/papers/{paper_id}/roll", summary="The exam roll of a paper: students who may sit it, barred students apart, and cases (teachers on duty, exam office)")
async def get_roll(paper_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await ExamDutyService(db).roll(paper_id, current_user)


@router.put("/papers/{paper_id}/roll", summary="Mark the exam roll: present, absent or late (teachers on duty on the exam day; the exam office any time)")
async def mark_roll(paper_id: uuid.UUID, payload: RollIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await ExamDutyService(db).mark(paper_id, [e.model_dump() for e in payload.entries], current_user)


@router.post("/papers/{paper_id}/cases", status_code=201, summary="Report an unfair means case in this paper (teachers on duty, exam office)")
async def report_case(paper_id: uuid.UUID, payload: CaseIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await ExamDutyService(db).report(paper_id, payload.student_id, payload.note, current_user)


@router.get("/offerings/{offering_id}/absences", summary="Students absent in this course's exam papers, for marks entry (the course's teacher, exam office)")
async def absences(offering_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await ExamDutyService(db).absences(offering_id, current_user)
