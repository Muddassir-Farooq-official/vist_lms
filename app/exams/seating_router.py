"""Exam seating and room invigilation API (app/exams/seating.py): the exam office and administrators."""
from __future__ import annotations

import uuid
from datetime import date, time

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission

router = APIRouter(prefix="/exams/seating", tags=["exam seating"])
office = require_permission(Permissions.EXAM_SCHEDULE_MANAGE)


def _svc(db):
    from app.exams.seating import SeatingService

    return SeatingService(db)


class RoomLayout(BaseModel):
    exam_rows: int | None = Field(default=None, ge=1, le=60)
    exam_cols: int | None = Field(default=None, ge=1, le=30)


@router.get("/rooms", summary="Rooms and their exam layout (rows x columns)")
async def rooms(current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).rooms()


@router.put("/rooms/{room_id}", summary="Set a room's exam rows and columns (empty both = not an exam room)")
async def set_room(room_id: uuid.UUID, payload: RoomLayout, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).set_room(room_id, rows=payload.exam_rows, cols=payload.exam_cols, actor_id=current_user.id)


@router.get("/{schedule_id}", summary="The datesheet's time slots: papers, students, seated, rooms, invigilators")
async def slots(schedule_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).slots(schedule_id)


class SlotIn(BaseModel):
    exam_date: date
    start_time: time
    room_ids: list[uuid.UUID] | None = None


@router.post("/{schedule_id}/seat", summary="Make (or remake) one slot's seating, mixing papers in neighbouring columns")
async def seat(schedule_id: uuid.UUID, payload: SlotIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).seat(schedule_id, exam_date=payload.exam_date, start_time=payload.start_time, room_ids=payload.room_ids, actor_id=current_user.id)


@router.get("/{schedule_id}/plan", summary="One slot: each room's seats and invigilators")
async def plan(schedule_id: uuid.UUID, exam_date: date = Query(...), start_time: time = Query(...), current_user: CurrentUser = Depends(office),
               db=Depends(get_db)) -> dict:
    return await _svc(db).room_plan(schedule_id, exam_date, start_time)


@router.post("/{schedule_id}/invigilators/auto", summary="Propose invigilators for every room of a slot (fair, free teachers, never the paper's own)")
async def auto(schedule_id: uuid.UUID, payload: SlotIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).auto_invigilate(schedule_id, exam_date=payload.exam_date, start_time=payload.start_time, actor_id=current_user.id)


class RoomDutyIn(BaseModel):
    exam_date: date
    start_time: time
    room_id: uuid.UUID
    employee_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    in_charge_id: uuid.UUID | None = None


@router.put("/{schedule_id}/invigilators", summary="Set one room's invigilators for a slot")
async def set_invigilators(schedule_id: uuid.UUID, payload: RoomDutyIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).set_invigilators(schedule_id, exam_date=payload.exam_date, start_time=payload.start_time, room_id=payload.room_id,
                                           employee_ids=payload.employee_ids, in_charge_id=payload.in_charge_id, actor_id=current_user.id)


@router.get("/{schedule_id}/teachers", summary="Teachers to choose from for a slot (free ones first)")
async def teachers(schedule_id: uuid.UUID, exam_date: date = Query(...), start_time: time = Query(...), end_time: time = Query(...),
                   current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    from app.exams.duty import ExamDutyService

    svc = _svc(db)
    busy = await svc._busy(exam_date, start_time, end_time, schedule_id)
    return sorted([{"employee_id": e.id, "name": f"{e.first_name} {e.last_name}", "code": e.employee_code, "free": e.id not in busy}
                   for e in await ExamDutyService(db)._pool()], key=lambda x: (not x["free"], x["name"].lower()))


@router.post("/{schedule_id}/tell", summary="Tell each invigilator their rooms and times (each duty once)")
async def tell(schedule_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).tell(schedule_id, actor_id=current_user.id)


def _pdf_response(data: bytes, name: str) -> Response:
    return Response(content=data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/{schedule_id}/door-lists.pdf", summary="Door list for each room of a slot")
async def door_lists(schedule_id: uuid.UUID, exam_date: date = Query(...), start_time: time = Query(...), current_user: CurrentUser = Depends(office),
                     db=Depends(get_db)) -> Response:
    from app.exams.seating import door_lists_pdf

    svc = _svc(db)
    sched = await svc._schedule(schedule_id)
    plan_ = await svc.room_plan(schedule_id, exam_date, start_time)
    return _pdf_response(await run_in_threadpool(door_lists_pdf, sched.name, plan_), f"door-lists-{exam_date}.pdf")


@router.get("/{schedule_id}/attendance-sheets.pdf", summary="Attendance sheet for each room of a slot")
async def attendance_sheets(schedule_id: uuid.UUID, exam_date: date = Query(...), start_time: time = Query(...), current_user: CurrentUser = Depends(office),
                            db=Depends(get_db)) -> Response:
    from app.exams.seating import attendance_sheets_pdf

    svc = _svc(db)
    sched = await svc._schedule(schedule_id)
    plan_ = await svc.room_plan(schedule_id, exam_date, start_time)
    return _pdf_response(await run_in_threadpool(attendance_sheets_pdf, sched.name, plan_), f"attendance-sheets-{exam_date}.pdf")


@router.get("/{schedule_id}/duty-sheet.pdf", summary="Each teacher's invigilation duties for the datesheet")
async def duty_sheet(schedule_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> Response:
    from app.exams.seating import duty_sheet_pdf

    sheet = await _svc(db).duty_sheet(schedule_id)
    return _pdf_response(await run_in_threadpool(duty_sheet_pdf, sheet), "invigilation-duties.pdf")
