"""School mode, stage 2 API: /api/v1/school/roll-call, /homework, /ptm and the student / parent views."""
from __future__ import annotations

import uuid
from datetime import date, time
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.core.clock import institute_today
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission
from app.core.exceptions import AuthorizationError, ResourceNotFoundError
from app.school.daily import HomeworkService, PtmService, RollCallService, can_see_student

router = APIRouter(prefix="/school", tags=["school"])
office = require_permission(Permissions.COURSE_MANAGE)


class RollEntry(BaseModel):
    student_id: uuid.UUID
    status: Literal["PRESENT", "ABSENT", "LATE", "LEAVE"]
    note: str | None = Field(default=None, max_length=200)


class RollIn(BaseModel):
    day: date | None = None
    entries: list[RollEntry] = Field(min_length=1, max_length=200)


class CoverIn(BaseModel):
    day: date
    employee_id: uuid.UUID | None = None


class PtmIn(BaseModel):
    day: date
    start_time: time
    end_time: time
    slot_minutes: int = Field(ge=5, le=60)
    note: str | None = Field(default=None, max_length=300)
    session_label: str | None = None


class BookIn(BaseModel):
    student_id: uuid.UUID


class NotesIn(BaseModel):
    notes: str | None = Field(default=None, max_length=1000)
    attended: bool | None = None


# ---------------------------------------------------------------- roll call
@router.get("/roll-call/mine", summary="Sections whose roll call I take today (class teacher or cover)")
async def roll_mine(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await RollCallService(db).mine(current_user)


@router.get("/roll-call/sections/{section_id}", summary="A section's roll call for a day (default today)")
async def roll_sheet(section_id: uuid.UUID, day: date | None = None, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await RollCallService(db).sheet(section_id, day or institute_today(), current_user)


@router.put("/roll-call/sections/{section_id}", summary="Save the roll call (teachers: today; the office: any past day). Parents of absent / late children are told")
async def roll_mark(section_id: uuid.UUID, payload: RollIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await RollCallService(db).mark(section_id, payload.day or institute_today(), [e.model_dump() for e in payload.entries], current_user)


@router.put("/roll-call/sections/{section_id}/cover", summary="Set (or clear) the teacher who takes this section's roll call on a day (office)")
async def roll_cover(section_id: uuid.UUID, payload: CoverIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await RollCallService(db).set_cover(section_id, payload.day, payload.employee_id, current_user.id)


@router.get("/roll-call/overview", summary="Every section's roll call on a day: taken or not, absent and late (office)")
async def roll_overview(day: date | None = None, session_label: str | None = None, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await RollCallService(db).overview(day or institute_today(), session_label)


@router.get("/students/{student_id}/days", summary="A student's daily attendance for a month (the student, a parent, a teacher of the section, the office)")
async def student_days(student_id: uuid.UUID, month: str | None = None, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    await can_see_student(db, current_user, student_id)
    m = month or institute_today().strftime("%Y-%m")
    try:
        y, mo = (int(x) for x in m.split("-"))
        date(y, mo, 1)
    except ValueError as exc:
        raise ResourceNotFoundError("Month must look like 2026-11.") from exc
    return await RollCallService(db).month(student_id, y, mo)


@router.get("/me/days", summary="My daily attendance for a month (students)")
async def my_days(month: str | None = None, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    sid = await _my_student_id(db, current_user)
    return await student_days(sid, month, current_user, db)


async def _my_student_id(db, user) -> uuid.UUID:
    from sqlalchemy import select

    from app.students.models import Student

    sid = (await db.execute(select(Student.id).where(Student.user_id == user.id))).scalar_one_or_none()
    if sid is None:
        raise AuthorizationError("Only students have a diary here.")
    return sid


# ---------------------------------------------------------------- homework
@router.get("/homework/mine", summary="The classes I teach and the homework I set in the last two weeks (teachers)")
async def homework_mine(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await HomeworkService(db).for_teacher(current_user)


@router.post("/homework", status_code=201, summary="Set homework for a section's subject (its teacher); a PDF or picture can be attached")
async def homework_post(offering_id: uuid.UUID = Form(...), title: str = Form(..., max_length=150), details: str | None = Form(default=None, max_length=2000),
                        due_on: date | None = Form(default=None), file: UploadFile | None = File(default=None),
                        current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    attached = None
    if file is not None and file.filename:
        attached = (await file.read(), file.filename, file.content_type or "")
    return await HomeworkService(db).post(current_user, offering_id=offering_id, title=title, details=details, due_on=due_on, file=attached)


@router.delete("/homework/{homework_id}", status_code=204, summary="Remove homework (its teacher, on the day it was set; the office any time)")
async def homework_remove(homework_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    await HomeworkService(db).remove(homework_id, current_user)
    return Response(status_code=204)


@router.get("/homework/{homework_id}/file", summary="Open the file attached to homework")
async def homework_file(homework_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    from app.core.storage import SAFE_SERVE_HEADERS, attachment_is_inline, get_file_path

    hw = await HomeworkService(db).file(homework_id, current_user)
    path = get_file_path(hw.file_key)
    if path is None or not path.exists():
        raise ResourceNotFoundError("The file is missing from storage.")
    disp = "inline" if attachment_is_inline(hw.file_type or "") else "attachment"
    safe = (hw.file_name or "homework").replace('"', "")
    return Response(content=path.read_bytes(), media_type=hw.file_type or "application/octet-stream",
                    headers={"Content-Disposition": f'{disp}; filename="{safe}"', "Cache-Control": "no-store", **SAFE_SERVE_HEADERS})


@router.get("/students/{student_id}/homework", summary="A student's homework diary for the last two weeks (the student, a parent, a teacher, the office)")
async def student_homework(student_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    await can_see_student(db, current_user, student_id)
    return await HomeworkService(db).for_student(student_id)


@router.get("/me/homework", summary="My homework diary (students)")
async def my_homework(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await HomeworkService(db).for_student(await _my_student_id(db, current_user))


# ---------------------------------------------------------------- parent-teacher meetings
@router.get("/ptm", summary="Parent-teacher meeting days (office)")
async def ptm_list(current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await PtmService(db).list()


@router.post("/ptm", status_code=201, summary="Set a parent-teacher meeting day: slots for every class teacher; parents are told (office)")
async def ptm_create(payload: PtmIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await PtmService(db).create(term_label=payload.session_label, day=payload.day, start=payload.start_time, end=payload.end_time,
                                       slot_minutes=payload.slot_minutes, note=payload.note, actor_id=current_user.id)


@router.post("/ptm/{ptm_id}/cancel", summary="Cancel a meeting day; parents who booked are told (office)")
async def ptm_cancel(ptm_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await PtmService(db).cancel(ptm_id, current_user.id)


@router.get("/ptm/mine", summary="My meeting slots and who booked them (class teachers)")
async def ptm_mine(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await PtmService(db).for_teacher(current_user)


@router.put("/ptm/slots/{slot_id}/notes", summary="Note what was discussed, and whether the parent came (the class teacher)")
async def ptm_notes(slot_id: uuid.UUID, payload: NotesIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await PtmService(db).save_notes(current_user, slot_id, payload.notes, payload.attended)


@router.get("/children/{student_id}/ptm", summary="Meeting days and the class teacher's free times for my child (parents)")
async def ptm_for_child(student_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.PARENT_VIEW)), db=Depends(get_db)) -> dict:
    return await PtmService(db).for_parent(current_user, student_id)


@router.post("/ptm/slots/{slot_id}/book", summary="Book a time with my child's class teacher (parents; one per child per day)")
async def ptm_book(slot_id: uuid.UUID, payload: BookIn, current_user: CurrentUser = Depends(require_permission(Permissions.PARENT_VIEW)),
                   db=Depends(get_db)) -> dict:
    return await PtmService(db).book(current_user, slot_id, payload.student_id)


@router.post("/ptm/slots/{slot_id}/release", summary="Cancel my booking (parents)")
async def ptm_release(slot_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.PARENT_VIEW)), db=Depends(get_db)) -> dict:
    return await PtmService(db).release(current_user, slot_id)

