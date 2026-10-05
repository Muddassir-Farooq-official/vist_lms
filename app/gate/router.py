from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.auth.permissions import Permissions
from app.core.clock import institute_today
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission
from app.core.exceptions import AuthorizationError
from app.gate.schemas import (
    BulkScheduleIn,
    FineDecision,
    FineRow,
    FiltersOut,
    Kind,
    LogEdit,
    LogOut,
    MarkIn,
    PeopleOut,
    PersonOut,
    ReportOut,
    ScheduleIn,
    ScheduleRow,
    SettingsIn,
    SettingsOut,
    SummaryOut,
)
from app.gate.service import GateService

router = APIRouter(prefix="/gate", tags=["gate"])
operate = require_permission(Permissions.GATE_OPERATE)
manage = require_permission(Permissions.GATE_MANAGE)
GUARD_LOOK_BACK_DAYS = 7


def _admin(user: CurrentUser) -> bool:
    return Permissions.GATE_MANAGE in user.permissions


# ============================================================ at the gate (the guard, and the administration)
@router.get("/filters", response_model=FiltersOut, summary="Programs, semesters and job roles to filter by")
async def filters(current_user: CurrentUser = Depends(operate), db=Depends(get_db)) -> FiltersOut:
    return FiltersOut(**await GateService(db).filters())


@router.get("/summary", response_model=SummaryOut, summary="Today at a glance: who is inside, who is late")
async def summary(current_user: CurrentUser = Depends(operate), db=Depends(get_db)) -> SummaryOut:
    return SummaryOut(**await GateService(db).summary())


@router.get("/people", response_model=PeopleOut, summary="Search students and staff (by name, roll number or code) and filter the list; each with today's state")
async def people(
    q: str | None = None, kind: Kind | None = None, program_id: uuid.UUID | None = None, semester: int | None = Query(default=None, ge=1, le=20),
    job_role_id: uuid.UUID | None = None, state: str | None = Query(default=None, pattern="^(NOT_ARRIVED|INSIDE|LEFT|LATE)$"),
    limit: int = 30, offset: int = 0, current_user: CurrentUser = Depends(operate), db=Depends(get_db),
) -> PeopleOut:
    r = await GateService(db).search(q=q, kind=kind.value if kind else None, program_id=program_id, semester=semester, job_role_id=job_role_id, state=state,
                                     limit=limit, offset=max(0, offset), guard_id=current_user.id, admin=_admin(current_user))
    return PeopleOut(people=[PersonOut(**p) for p in r["people"]], has_more=r["has_more"])


@router.post("/check-in", response_model=PersonOut, summary="Mark a person in now (late is worked out from their timing)")
async def check_in(payload: MarkIn, current_user: CurrentUser = Depends(operate), db=Depends(get_db)) -> PersonOut:
    return PersonOut(**await GateService(db).check_in(guard_id=current_user.id, kind=payload.kind.value, person_id=payload.person_id, admin=_admin(current_user)))


@router.post("/check-out", response_model=PersonOut, summary="Mark a person out now")
async def check_out(payload: MarkIn, current_user: CurrentUser = Depends(operate), db=Depends(get_db)) -> PersonOut:
    return PersonOut(**await GateService(db).check_out(guard_id=current_user.id, kind=payload.kind.value, person_id=payload.person_id, admin=_admin(current_user)))


@router.post("/logs/{log_id}/undo", response_model=PersonOut | None, summary="Take back my latest mark (within 10 minutes; the administration any time)")
async def undo(log_id: uuid.UUID, current_user: CurrentUser = Depends(operate), db=Depends(get_db)):
    card = await GateService(db).undo(log_id, guard_id=current_user.id, admin=_admin(current_user))
    return PersonOut(**card)


@router.get("/logs", response_model=list[LogOut], summary="Who came in and went out on a day (a guard sees today and the last 7 days)")
async def logs(
    day: date | None = None, kind: Kind | None = None, late_only: bool = False, q: str | None = None, limit: int = 100, offset: int = 0,
    current_user: CurrentUser = Depends(operate), db=Depends(get_db),
) -> list[LogOut]:
    today = institute_today()
    day = day or today
    if not _admin(current_user) and (day > today or day < today - timedelta(days=GUARD_LOOK_BACK_DAYS)):
        raise AuthorizationError(f"You can look at today and the last {GUARD_LOOK_BACK_DAYS} days.")
    rows = await GateService(db).logs(day=day, kind=kind.value if kind else None, late_only=late_only, q=q, limit=limit, offset=offset, guard_id=current_user.id, admin=_admin(current_user))
    return [LogOut(**r) for r in rows]


# ============================================================ the administration
@router.get("/settings", response_model=SettingsOut, summary="The students' timing and fine rule, and the default staff timing")
async def get_settings(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> SettingsOut:
    service = GateService(db)
    return SettingsOut(**service.settings_out(await service.settings()))


@router.put("/settings", response_model=SettingsOut, summary="Change those rules")
async def put_settings(payload: SettingsIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> SettingsOut:
    service = GateService(db)
    return SettingsOut(**service.settings_out(await service.update_settings(payload.model_dump(), admin_id=current_user.id)))


@router.get("/schedules", response_model=list[ScheduleRow], summary="Every employee's timing (their own, or the default)")
async def schedules(q: str | None = None, job_role_id: uuid.UUID | None = None, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[ScheduleRow]:
    return [ScheduleRow(**r) for r in await GateService(db).list_schedules(q=q, job_role_id=job_role_id)]


@router.post("/schedules/bulk", summary="Give the same timing to everyone, or to everyone in one job role")
async def bulk(payload: BulkScheduleIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    n = await GateService(db).bulk_schedule(payload.model_dump(), admin_id=current_user.id)
    return {"updated": n}


@router.put("/schedules/{employee_id}", status_code=204, summary="Set one employee's arrival and leaving time and working days")
async def set_schedule(employee_id: uuid.UUID, payload: ScheduleIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> Response:
    await GateService(db).set_schedule(employee_id, payload.model_dump(), admin_id=current_user.id)
    return Response(status_code=204)


@router.delete("/schedules/{employee_id}", status_code=204, summary="Remove an employee's own timing so the default applies")
async def clear_schedule(employee_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> Response:
    await GateService(db).clear_schedule(employee_id, admin_id=current_user.id)
    return Response(status_code=204)


@router.patch("/logs/{log_id}", response_model=LogOut, summary="Correct the times on an entry (lateness and any fine request are worked out again)")
async def edit_log(log_id: uuid.UUID, payload: LogEdit, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> LogOut:
    return LogOut(**await GateService(db).edit_log(log_id, admin_id=current_user.id, check_in_time=payload.check_in_time, check_out_time=payload.check_out_time,
                                                   clear_check_out=payload.clear_check_out, note=payload.note))


@router.delete("/logs/{log_id}", status_code=204, summary="Remove a wrong entry")
async def delete_log(log_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> Response:
    await GateService(db).delete_log(log_id, admin_id=current_user.id)
    return Response(status_code=204)


@router.get("/fines", response_model=list[FineRow], summary="Late-arrival fines: waiting for a decision, approved, or rejected")
async def fines(status: str | None = Query(default="PENDING_APPROVAL", pattern="^(PENDING_APPROVAL|APPROVED|REJECTED|ALL)$"), current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[FineRow]:
    return [FineRow(**r) for r in await GateService(db).fines(status)]


@router.post("/fines/{fine_id}/decide", response_model=FineRow, summary="Approve a late-arrival fine (optionally at a different amount) or reject it")
async def decide(fine_id: uuid.UUID, payload: FineDecision, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> FineRow:
    return FineRow(**await GateService(db).decide_fine(fine_id, admin_id=current_user.id, approve=payload.action.value == "APPROVE", amount=payload.amount, note=payload.note))


@router.get("/report", response_model=ReportOut, summary="Lateness over the last 30 / 60 / 90 days or all time")
async def report(days: int = Query(30, ge=0, le=3650), kind: Kind | None = None, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> ReportOut:
    return ReportOut(**await GateService(db).report(days, kind.value if kind else None))