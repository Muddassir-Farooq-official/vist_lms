"""Clinical rotations API (app/clinical/service.py)."""
from __future__ import annotations

import uuid
from datetime import date, time

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_any_permission, require_permission
from app.core.exceptions import AuthorizationError, ResourceNotFoundError

router = APIRouter(prefix="/clinical", tags=["clinical"])
office = require_permission(Permissions.CLINICAL_MANAGE)
supervising = require_any_permission(Permissions.CLINICAL_SUPERVISE, Permissions.CLINICAL_MANAGE)


def _svc(db):
    from app.clinical.service import ClinicalService

    return ClinicalService(db)


def _mgr(user: CurrentUser) -> bool:
    return Permissions.CLINICAL_MANAGE in user.permissions


async def _student(db, user):
    from app.students.models import Student

    s = (await db.execute(select(Student).where(Student.user_id == user.id))).scalar_one_or_none()
    if s is None:
        raise AuthorizationError("This is for students.")
    return s


# ---------------------------------------------------------------- administration
@router.get("/setup", summary="Hospitals, wards, groups, supervisors and logbook skills")
async def setup(current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).setup()


class SiteIn(BaseModel):
    name: str = Field(max_length=150)
    address: str | None = Field(default=None, max_length=300)
    contact_person: str | None = Field(default=None, max_length=120)
    contact_phone: str | None = Field(default=None, max_length=30)
    is_active: bool = True


@router.post("/sites", summary="Add a hospital")
async def add_site(payload: SiteIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).save_site(payload.model_dump())


@router.put("/sites/{site_id}", summary="Change a hospital")
async def edit_site(site_id: uuid.UUID, payload: SiteIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).save_site(payload.model_dump(), site_id=site_id)


class UnitIn(BaseModel):
    name: str = Field(max_length=120)
    capacity: int = Field(ge=1, le=200)


@router.post("/sites/{site_id}/units", summary="Add a ward to a hospital")
async def add_unit(site_id: uuid.UUID, payload: UnitIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).save_unit(site_id, name=payload.name, capacity=payload.capacity)


@router.put("/sites/{site_id}/units/{unit_id}", summary="Change a ward")
async def edit_unit(site_id: uuid.UUID, unit_id: uuid.UUID, payload: UnitIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).save_unit(site_id, name=payload.name, capacity=payload.capacity, unit_id=unit_id)


class GroupIn(BaseModel):
    name: str = Field(max_length=100)
    program_id: uuid.UUID | None = None
    semester: int | None = Field(default=None, ge=1, le=16)
    student_ids: list[uuid.UUID] = Field(default_factory=list, max_length=400)
    roll_numbers: list[str] = Field(default_factory=list, max_length=400)      # or the students' roll numbers


async def _group_ids(db, payload: GroupIn) -> list[uuid.UUID]:
    from app.core.exceptions import ValidationAppError
    from app.students.models import Student

    rolls = [r.strip() for r in payload.roll_numbers if r.strip()]
    found = dict((await db.execute(select(Student.roll_number, Student.id).where(Student.roll_number.in_(rolls or ["-"])))).all())
    missing = [r for r in rolls if r not in found]
    if missing:
        raise ValidationAppError("Roll numbers not found: " + ", ".join(missing[:10]))
    return list(dict.fromkeys(list(payload.student_ids) + [found[r] for r in rolls]))


@router.post("/groups", summary="Make a clinical group")
async def add_group(payload: GroupIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).save_group(name=payload.name, program_id=payload.program_id, semester=payload.semester, student_ids=await _group_ids(db, payload))


@router.put("/groups/{group_id}", summary="Rename a group or change its students")
async def edit_group(group_id: uuid.UUID, payload: GroupIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).save_group(name=payload.name, program_id=payload.program_id, semester=payload.semester, student_ids=await _group_ids(db, payload),
                                     group_id=group_id)


@router.get("/groups/{group_id}/students", summary="A group's students")
async def group_students(group_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).group_students(group_id)


@router.get("/groups/{group_id}/report", summary="Clinical attendance and logbook progress of a group")
async def group_report(group_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).group_report(group_id)


class SkillIn(BaseModel):
    program_id: uuid.UUID
    name: str = Field(max_length=150)
    required: int = Field(ge=1, le=500)
    semester: int | None = Field(default=None, ge=1, le=16)
    is_active: bool = True


@router.post("/skills", summary="Add a logbook skill a program requires")
async def add_skill(payload: SkillIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).save_skill(**payload.model_dump())


@router.put("/skills/{skill_id}", summary="Change a logbook skill")
async def edit_skill(skill_id: uuid.UUID, payload: SkillIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).save_skill(**payload.model_dump(), skill_id=skill_id)


class SupervisorIn(BaseModel):
    employee_id: uuid.UUID | None = None
    employee_code: str | None = Field(default=None, max_length=20)      # or one of our staff by employee code
    name: str | None = Field(default=None, max_length=120)
    username: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=30)
    site_id: uuid.UUID | None = None


@router.post("/supervisors", summary="Add a supervisor: one of our staff, or a hospital's staff member with a limited login")
async def add_supervisor(payload: SupervisorIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    from app.employees.models import Employee

    data = payload.model_dump()
    code = (data.pop("employee_code") or "").strip()
    if code and not data["employee_id"]:
        data["employee_id"] = (await db.execute(select(Employee.id).where(Employee.employee_code == code))).scalar_one_or_none()
        if data["employee_id"] is None:
            raise ResourceNotFoundError("No employee with that code.")
    return await _svc(db).add_supervisor(**data, actor_id=current_user.id)


class ActiveIn(BaseModel):
    is_active: bool


@router.post("/supervisors/{supervisor_id}/active", summary="Switch a supervisor (and a hospital login) on or off")
async def supervisor_active(supervisor_id: uuid.UUID, payload: ActiveIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).set_supervisor_active(supervisor_id, payload.is_active)


class RotationIn(BaseModel):
    group_id: uuid.UUID
    unit_id: uuid.UUID
    supervisor_id: uuid.UUID | None = None
    start_date: date
    end_date: date
    days: str = Field(default="0,1,2,3,4", max_length=20)
    start_time: time
    end_time: time
    note: str | None = Field(default=None, max_length=300)


@router.get("/rotations", summary="The rotation schedule")
async def rotations(group_id: uuid.UUID | None = None, current: bool = False, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).rotations(group_id=group_id, current_only=current)


@router.post("/rotations", summary="Schedule a rotation (refused if the group is elsewhere or the ward is full)")
async def add_rotation(payload: RotationIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).save_rotation(payload.model_dump(), actor_id=current_user.id)


@router.put("/rotations/{rotation_id}", summary="Change a rotation")
async def edit_rotation(rotation_id: uuid.UUID, payload: RotationIn, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    return await _svc(db).save_rotation(payload.model_dump(), actor_id=current_user.id, rotation_id=rotation_id)


@router.delete("/rotations/{rotation_id}", summary="Remove a rotation that has no attendance or logbook entries")
async def delete_rotation(rotation_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> dict:
    await _svc(db).delete_rotation(rotation_id)
    return {"deleted": True}


@router.get("/rotations/{rotation_id}/letter.pdf", summary="Placement letter for the hospital")
async def letter(rotation_id: uuid.UUID, current_user: CurrentUser = Depends(office), db=Depends(get_db)) -> Response:
    from app.clinical.models import ClinicalRotation, ClinicalSite
    from app.clinical.service import placement_letter_pdf
    from app.core.clock import institute_today

    svc = _svc(db)
    r = await db.get(ClinicalRotation, rotation_id)
    if r is None:
        raise ResourceNotFoundError("Rotation not found.")
    row = (await svc._rot_rows([r]))[0]
    site = await db.get(ClinicalSite, row["site_id"])
    body = await run_in_threadpool(placement_letter_pdf, row, {"name": site.name, "address": site.address, "contact_person": site.contact_person},
                                   await svc.group_students(r.group_id), institute_today())
    return Response(content=body, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="placement-{r.start_date:%Y%m%d}.pdf"', "Cache-Control": "no-store"})


# ---------------------------------------------------------------- supervisors
@router.get("/supervise", summary="My rotations as a clinical supervisor")
async def my_rotations(current_user: CurrentUser = Depends(supervising), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).my_rotations(current_user)


@router.get("/supervise/{rotation_id}/day", summary="A day's attendance sheet")
async def day_sheet(rotation_id: uuid.UUID, day: date, current_user: CurrentUser = Depends(supervising), db=Depends(get_db)) -> dict:
    return await _svc(db).day_sheet(rotation_id, day, current_user, manager=_mgr(current_user))


class MarkIn(BaseModel):
    day: date
    entries: list[dict] = Field(max_length=400)


@router.post("/supervise/{rotation_id}/attendance", summary="Mark a day's clinical attendance")
async def mark(rotation_id: uuid.UUID, payload: MarkIn, current_user: CurrentUser = Depends(supervising), db=Depends(get_db)) -> dict:
    return await _svc(db).mark(rotation_id, payload.day, payload.entries, current_user, manager=_mgr(current_user))


@router.get("/supervise/{rotation_id}/logbook", summary="Logbook entries of a rotation (waiting ones first)")
async def to_sign(rotation_id: uuid.UUID, current_user: CurrentUser = Depends(supervising), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).to_sign(rotation_id, current_user, manager=_mgr(current_user))


class SignIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=300)


@router.post("/logbook/{entry_id}/sign", summary="Sign or reject a logbook entry")
async def sign(entry_id: uuid.UUID, payload: SignIn, current_user: CurrentUser = Depends(supervising), db=Depends(get_db)) -> dict:
    return await _svc(db).sign(entry_id, approve=payload.approve, note=payload.note, user=current_user, manager=_mgr(current_user))


# ---------------------------------------------------------------- students
@router.get("/me", summary="My rotations, clinical attendance and logbook progress (student)")
async def me(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await _svc(db).student_view(await _student(db, current_user))


class LogIn(BaseModel):
    skill_id: uuid.UUID
    rotation_id: uuid.UUID
    day: date
    times: int = Field(default=1, ge=1, le=50)
    note: str | None = Field(default=None, max_length=500)


@router.post("/me/logbook", summary="Add a logbook entry for the supervisor to sign")
async def log(payload: LogIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await _svc(db).log(await _student(db, current_user), **payload.model_dump())


@router.delete("/me/logbook/{entry_id}", summary="Withdraw an entry not yet signed")
async def withdraw(entry_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    await _svc(db).withdraw(await _student(db, current_user), entry_id)
    return {"deleted": True}
