"""Smart timetable API (app/timetabling/service.py). Administrators (course-offering managers) generate, adjust and
publish drafts and set the per-section settings and teachers' unavailable times; teachers set their own times."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app.auth.permissions import Permissions, Roles
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_any_role, require_permission, require_any_permission
from app.core.exceptions import AuthorizationError, ValidationAppError
from app.timetabling.service import WEEK, AvailabilityService, SmartTimetableService, next_monday

router = APIRouter(prefix="/academics/timetable/smart", tags=["smart-timetable"])
manage = require_any_permission(Permissions.TIMETABLE_MANAGE, Permissions.COURSE_OFFERING_MANAGE)
teacher_only = require_any_role(Roles.TEACHER)


def _terms(raw: str) -> list[str]:
    return [t for t in (raw or "").split("||") if t.strip()]


async def _scoped(db, user, labels) -> list[str]:
    """The batch terms, refused if any is outside this person's program limits (Access control -> People)."""
    from app.access.scope import allows_terms, get_scope

    labels = list(labels)
    if not await allows_terms(db, await get_scope(db, user), labels):
        raise AuthorizationError("Some of these batches are outside the programs you are limited to.")
    return labels


async def _offering_scoped(db, user, offering_ids) -> None:
    from app.academics.models import CourseOffering
    from app.access.scope import get_scope

    scope = await get_scope(db, user)
    if scope is None or not scope.programs:
        return
    from sqlalchemy import select as _select

    labels = (await db.execute(_select(CourseOffering.term_label).where(CourseOffering.id.in_(list(offering_ids))))).scalars().all()
    await _scoped(db, user, set(labels))


async def _draft_scoped(db, user, draft_id) -> None:
    from app.access.scope import get_scope
    from app.timetabling.models import TimetableDraft

    scope = await get_scope(db, user)
    if scope is None or not scope.programs:
        return
    draft = await db.get(TimetableDraft, draft_id)
    if draft is not None:
        await _scoped(db, user, draft.terms or [])


def _no_view_as(user: CurrentUser) -> None:
    if getattr(user, "viewing_as_by_id", None) is not None:
        raise AuthorizationError("Not while viewing someone else's screens.")


class GenerateIn(BaseModel):
    terms: list[str] = Field(min_length=1, max_length=30)
    days: list[str] = Field(min_length=1, max_length=7)
    max_per_day: int | None = Field(default=None, ge=1, le=12)
    max_in_row: int | None = Field(default=None, ge=1, le=12)
    keep_pinned_from: uuid.UUID | None = None


class PlaceIn(BaseModel):
    offering_id: uuid.UUID | None = None
    day: str
    period_id: uuid.UUID
    room: str = Field(min_length=1, max_length=50)


class PublishIn(BaseModel):
    from_date: date
    notify: list[str] = Field(default_factory=list, max_length=3)


class SectionIn(BaseModel):
    classes_per_week: int | None = Field(default=None, ge=1, le=7)
    double_period: bool = False
    home_room: str | None = Field(default=None, max_length=50)


class AvailabilityIn(BaseModel):
    blocked: list[str] = Field(default_factory=list, max_length=400)
    max_per_day: int | None = Field(default=None, ge=1, le=12)
    max_in_row: int | None = Field(default=None, ge=1, le=12)
    note: str | None = Field(default=None, max_length=300)
    locked: bool | None = None


def _day(v: str) -> str:
    v = (v or "").upper()
    if v not in WEEK:
        raise ValidationAppError("Unknown day.")
    return v


# ------------------------------------------------------------------ drafts
@router.get("/latest", summary="The newest draft (or the last published one), if any")
async def latest(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    d = await SmartTimetableService(db).latest()
    if d is not None:
        from app.access.scope import allows_terms, get_scope

        if not await allows_terms(db, await get_scope(db, current_user), d.terms or []):
            d = None                                  # someone else's programs: start a new draft for their own
    return {"id": d.id if d else None, "status": d.status if d else None, "next_monday": next_monday()}


@router.post("/drafts", summary="Generate a draft timetable for whole terms (nothing live changes)")
async def generate(payload: GenerateIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    _no_view_as(current_user)
    svc = SmartTimetableService(db)
    draft = await svc.generate(terms=await _scoped(db, current_user, payload.terms), days=[_day(x) for x in payload.days], max_per_day=payload.max_per_day,
                               max_in_row=payload.max_in_row, actor_id=current_user.id, keep_from=payload.keep_pinned_from)
    return await svc.view(draft.id)


@router.get("/drafts/{draft_id}", summary="A draft: its classes, what could not be placed, and the choices for each view")
async def view(draft_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    await _draft_scoped(db, current_user, draft_id)
    return await SmartTimetableService(db).view(draft_id)


@router.post("/drafts/{draft_id}/slots", summary="Place a class by hand (checked; kept when the draft is generated again)")
async def add(draft_id: uuid.UUID, payload: PlaceIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    await _draft_scoped(db, current_user, draft_id)
    _no_view_as(current_user)
    if payload.offering_id is None:
        raise ValidationAppError("Choose the course.")
    svc = SmartTimetableService(db)
    await svc.add(draft_id, offering_id=payload.offering_id, day=_day(payload.day), period_id=payload.period_id, room=payload.room,
                  actor_id=current_user.id)
    return await svc.view(draft_id)


@router.patch("/drafts/{draft_id}/slots/{slot_id}", summary="Move a class in the draft (checked; it becomes pinned)")
async def move(draft_id: uuid.UUID, slot_id: uuid.UUID, payload: PlaceIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    await _draft_scoped(db, current_user, draft_id)
    _no_view_as(current_user)
    svc = SmartTimetableService(db)
    await svc.move(draft_id, slot_id, day=_day(payload.day), period_id=payload.period_id, room=payload.room, actor_id=current_user.id)
    return await svc.view(draft_id)


@router.delete("/drafts/{draft_id}/slots/{slot_id}", summary="Take a class out of the draft")
async def remove(draft_id: uuid.UUID, slot_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    await _draft_scoped(db, current_user, draft_id)
    _no_view_as(current_user)
    svc = SmartTimetableService(db)
    await svc.remove(draft_id, slot_id, actor_id=current_user.id)
    return await svc.view(draft_id)


@router.post("/drafts/{draft_id}/discard", summary="Throw the draft away (the live timetable is not touched)")
async def discard(draft_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    await _draft_scoped(db, current_user, draft_id)
    _no_view_as(current_user)
    await SmartTimetableService(db).discard(draft_id, actor_id=current_user.id)
    return {"ok": True}


@router.get("/drafts/{draft_id}/publish-preview", summary="What publishing from a date would do (nothing is changed)")
async def publish_preview(draft_id: uuid.UUID, from_date: date = Query(...), current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    await _draft_scoped(db, current_user, draft_id)
    return await SmartTimetableService(db).publish_preview(draft_id, from_date)


@router.post("/drafts/{draft_id}/publish", summary="Make the draft the live timetable from a date (all or nothing)")
async def publish(draft_id: uuid.UUID, payload: PublishIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    await _draft_scoped(db, current_user, draft_id)
    _no_view_as(current_user)
    notify = {x for x in payload.notify if x in ("teachers", "students", "parents")}
    # Approval rule (Access control -> Approvals): if publishing needs someone's approval, it waits for them
    # (My decisions); without a rule it is published at once, as always.
    from app.approvals import rules
    from app.approvals.service import gate, pending_response

    res = await rules.effective(db, "TIMETABLE_PUBLISH", requester_id=current_user.id,
                                ctx={"department": await rules.department_of(db, current_user.id)})
    if not (res.default or res.no_approval):
        r = await gate(db, kind="TIMETABLE_PUBLISH", actor_id=current_user.id, target_id=draft_id,
                       payload={"draft_id": str(draft_id), "from_date": payload.from_date.isoformat(), "notify": sorted(notify)},
                       summary=f"Publish a new timetable from {payload.from_date:%d %b %Y}")
        if r is not None:
            out = pending_response(r)
            out["message"] = f"Sent for approval ({res.text}). The timetable is published when they approve."
            return out
    return await SmartTimetableService(db).publish(draft_id, from_date=payload.from_date, notify=notify, actor_id=current_user.id)


# ------------------------------------------------------------------ start again: clear the live timetable
@router.get("/clear-preview", summary="What clearing the weekly timetable of these terms from a date would do (nothing is changed)")
async def clear_preview(terms: str = Query(...), from_date: date = Query(...), current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SmartTimetableService(db).clear_preview(await _scoped(db, current_user, _terms(terms)), from_date)


class ClearIn(BaseModel):
    terms: list[str] = Field(min_length=1, max_length=30)
    from_date: date
    confirm: str = Field(max_length=10)
    notify: list[str] = Field(default_factory=list, max_length=3)


@router.post("/clear", summary="Clear the weekly timetable of these terms from a date (classes held or with attendance are kept; all or nothing)")
async def clear(payload: ClearIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    from app.core.exceptions import ValidationAppError

    _no_view_as(current_user)
    if payload.confirm.strip().upper() != "CLEAR":
        raise ValidationAppError('Type CLEAR to confirm.')
    notify = {x for x in payload.notify if x in ("teachers", "students", "parents")}
    return await SmartTimetableService(db).clear(await _scoped(db, current_user, payload.terms), from_date=payload.from_date, notify=notify, actor_id=current_user.id)


@router.get("/readiness", summary="What is in place for these terms: periods, rooms, sections, teachers, live timetable, draft")
async def readiness(terms: str = Query(...), current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SmartTimetableService(db).readiness(await _scoped(db, current_user, _terms(terms)))


# ------------------------------------------------------------------ per-section settings
@router.get("/sections", summary="Classes a week, double period and home room of every section in the terms")
async def sections(terms: str = Query(...), current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    return await SmartTimetableService(db).sections(await _scoped(db, current_user, _terms(terms)))


@router.patch("/sections/{offering_id}", summary="Change a section's timetable settings (a combined class: all its sections)")
async def update_section(offering_id: uuid.UUID, payload: SectionIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    _no_view_as(current_user)
    await _offering_scoped(db, current_user, [offering_id])
    o = await SmartTimetableService(db).update_section(offering_id, classes_per_week=payload.classes_per_week, double_period=payload.double_period,
                                                       home_room=payload.home_room, actor_id=current_user.id)
    return {"id": o.id, "classes_per_week": o.classes_per_week, "double_period": o.double_period, "home_room": o.home_room}


# ------------------------------------------------------------------ teachers' unavailable times
@router.get("/teachers", summary="Teachers, with whether they have set unavailable times")
async def teachers(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    from app.academics.models import CourseOffering
    from app.employees.models import Employee, JobRole
    from app.timetabling.models import TeacherAvailability

    from app.core.clock import institute_today

    assigned = select(CourseOffering.assigned_teacher_id).where(
        CourseOffering.assigned_teacher_id.is_not(None),
        or_(CourseOffering.term_end_date.is_(None), CourseOffering.term_end_date >= institute_today()))
    rows = (await db.execute(select(Employee).join(JobRole, JobRole.id == Employee.job_role_id).where(
        Employee.left_on.is_(None), or_(JobRole.default_role_code == Roles.TEACHER, Employee.id.in_(assigned)))
        .order_by(Employee.first_name, Employee.last_name))).scalars().all()
    avail = {a.employee_id: a for a in (await db.execute(select(TeacherAvailability).where(
        TeacherAvailability.employee_id.in_([e.id for e in rows])))).scalars()} if rows else {}
    return [{"id": e.id, "name": f"{e.first_name} {e.last_name}".strip(), "code": e.employee_code, "department": e.department,
             "blocked": len(avail[e.id].blocked or []) if e.id in avail else 0,
             "max_per_day": avail[e.id].max_per_day if e.id in avail else None,
             "max_in_row": avail[e.id].max_in_row if e.id in avail else None,
             "locked": bool(e.id in avail and avail[e.id].locked)} for e in rows]


@router.get("/teachers/{employee_id}/availability", summary="A teacher's unavailable times and limits")
async def get_availability(employee_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await AvailabilityService(db).get(employee_id)


@router.put("/teachers/{employee_id}/availability", summary="Set a teacher's unavailable times and limits (administrator)")
async def set_availability(employee_id: uuid.UUID, payload: AvailabilityIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    _no_view_as(current_user)
    return await AvailabilityService(db).set(employee_id, blocked=payload.blocked, max_per_day=payload.max_per_day, max_in_row=payload.max_in_row,
                                             note=payload.note, actor_id=current_user.id, by_admin=True, locked=payload.locked)


async def _me(db, current_user: CurrentUser):
    from app.employees.repository import EmployeeRepository

    e = await EmployeeRepository(db).get_by_user_id(current_user.id)
    if e is None:
        raise AuthorizationError("This account is not linked to an employee record.")
    return e


@router.get("/my-availability", summary="My unavailable times (teacher)")
async def my_availability(current_user: CurrentUser = Depends(teacher_only), db=Depends(get_db)) -> dict:
    return await AvailabilityService(db).get((await _me(db, current_user)).id)


@router.put("/my-availability", summary="Set my unavailable times (teacher; not when the administration has fixed them)")
async def set_my_availability(payload: AvailabilityIn, current_user: CurrentUser = Depends(teacher_only), db=Depends(get_db)) -> dict:
    _no_view_as(current_user)
    me = await _me(db, current_user)
    return await AvailabilityService(db).set(me.id, blocked=payload.blocked, max_per_day=payload.max_per_day, max_in_row=payload.max_in_row,
                                             note=payload.note, actor_id=current_user.id, by_admin=False)


# ------------------------------------------------------------------ teacher workload and free times (app/timetabling/workload.py)
class RangeIn(BaseModel):
    load_min: int | None = Field(default=None, ge=0, le=60)
    load_max: int | None = Field(default=None, ge=0, le=60)


class AssignItem(BaseModel):
    offering_id: uuid.UUID
    teacher_id: uuid.UUID


class AssignIn(BaseModel):
    items: list[AssignItem] = Field(min_length=1, max_length=300)


@router.get("/workload", summary="Each teacher's load (credit hours a week) at the time of the chosen terms, against their range")
async def workload(terms: str = Query(...), current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    from app.timetabling.workload import WorkloadService

    return await WorkloadService(db).report(await _scoped(db, current_user, _terms(terms)))


@router.put("/workload/teachers/{employee_id}/range", summary="A teacher's own load range (empty = the institute's)")
async def set_range(employee_id: uuid.UUID, payload: RangeIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    from app.timetabling.workload import WorkloadService

    _no_view_as(current_user)
    return await WorkloadService(db).set_range(employee_id, load_min=payload.load_min, load_max=payload.load_max, actor_id=current_user.id)


@router.get("/workload/candidates/{offering_id}", summary="Teachers for a section, best first, with their load before and after")
async def candidates(offering_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    from app.timetabling.workload import WorkloadService

    return await WorkloadService(db).candidates(offering_id)


@router.get("/workload/proposals", summary="Proposed teachers for every section without one, keeping everyone within their range")
async def proposals(terms: str = Query(...), current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    from app.timetabling.workload import WorkloadService

    return await WorkloadService(db).propose(await _scoped(db, current_user, _terms(terms)))


@router.post("/workload/assign", summary="Assign the chosen teachers (all or nothing, every normal check)")
async def assign(payload: AssignIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    from app.timetabling.workload import WorkloadService

    _no_view_as(current_user)
    await _offering_scoped(db, current_user, [i.offering_id for i in payload.items])
    n = await WorkloadService(db).apply([(i.offering_id, i.teacher_id) for i in payload.items], actor_id=current_user.id)
    return {"assigned": n}


@router.get("/free", summary="What is free when: rooms and teachers per day and period, and each class's busy periods")
async def free(terms: str = Query(...), days: str | None = Query(default=None), current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    from app.timetabling.workload import WorkloadService

    return await WorkloadService(db).free_map(await _scoped(db, current_user, _terms(terms)), [_day(d) for d in days.split(",")] if days else None)
