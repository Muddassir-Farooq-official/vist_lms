from __future__ import annotations

import uuid
from datetime import date, time

from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_any_permission, require_permission
from app.core.exceptions import BusinessRuleViolationError
from app.timekeeping.service import TimekeepingService, now_local

router = APIRouter(prefix="/staff-attendance", tags=["staff attendance"])
mark_perm = require_permission(Permissions.STAFF_ATTENDANCE_MARK)
view_perm = require_any_permission(Permissions.STAFF_ATTENDANCE_VIEW, Permissions.STAFF_ATTENDANCE_MARK)
report_perm = require_permission(Permissions.STAFF_ATTENDANCE_VIEW)
manage_perm = require_permission(Permissions.STAFF_ATTENDANCE_MANAGE)


class TimingIn(BaseModel):
    in_time: time
    out_time: time
    grace_in_minutes: int = Field(default=10, ge=0, le=180)
    grace_out_minutes: int = Field(default=0, ge=0, le=180)
    working_days: list[int] = Field(min_length=1, max_length=7)


class MarkIn(BaseModel):
    employee_id: uuid.UUID
    action: str = Field(pattern="^(IN|OUT)$")


class CorrectIn(BaseModel):
    check_in: time | None = None
    check_out: time | None = None
    excused: bool | None = None
    excuse_reason: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=300)
    clear: bool = False


class HolidayIn(BaseModel):
    day: date
    reason: str = Field(min_length=3, max_length=200)


@router.get("/board", summary="Staff with their timing and the day's in / out (today unless a day is given)")
async def board(day: date | None = None, q: str | None = None, state: str | None = None,
                current_user: CurrentUser = Depends(view_perm), db=Depends(get_db)) -> dict:
    today = now_local().date()
    day = day or today
    if day != today and Permissions.STAFF_ATTENDANCE_VIEW not in current_user.permissions:
        raise BusinessRuleViolationError("You can see today only.")
    return {"day": day, "today": today, "now": now_local().strftime("%H:%M"),
            "can_manage": Permissions.STAFF_ATTENDANCE_MANAGE in current_user.permissions,
            "people": await TimekeepingService(db).board(day=day, q=q, state=state)}


@router.post("/mark", summary="Mark a member of staff in or out now")
async def mark(payload: MarkIn, current_user: CurrentUser = Depends(mark_perm), db=Depends(get_db)) -> dict:
    return await TimekeepingService(db).mark(payload.employee_id, payload.action, actor_id=current_user.id)


@router.post("/undo/{employee_id}", summary="Take back today's latest mark (your own within 10 minutes; the administration any time)")
async def undo(employee_id: uuid.UUID, current_user: CurrentUser = Depends(mark_perm), db=Depends(get_db)) -> dict:
    return await TimekeepingService(db).undo(employee_id, actor_id=current_user.id, admin=Permissions.STAFF_ATTENDANCE_MANAGE in current_user.permissions)


@router.put("/days/{employee_id}/{day}", summary="Correct a day: arrival / leaving time, excuse it (official duty, approved leave), or clear it (administration)")
async def correct(employee_id: uuid.UUID, day: date, payload: CorrectIn, current_user: CurrentUser = Depends(manage_perm), db=Depends(get_db)) -> dict:
    return await TimekeepingService(db).correct(employee_id, day, payload.model_dump(exclude_unset=True), actor_id=current_user.id)


@router.post("/holiday", summary="Excuse a whole day for every member of staff (public holiday, closure)")
async def holiday(payload: HolidayIn, current_user: CurrentUser = Depends(manage_perm), db=Depends(get_db)) -> dict:
    return {"employees": await TimekeepingService(db).holiday(payload.day, payload.reason, actor_id=current_user.id)}


@router.post("/import", summary="Import arrival / leaving times from a CSV (employee_code,date,check_in,check_out). dry_run=true only checks")
async def import_csv(file: UploadFile, dry_run: bool = True, current_user: CurrentUser = Depends(manage_perm), db=Depends(get_db)) -> dict:
    raw = await file.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise BusinessRuleViolationError("The file is larger than 2 MB.")
    return await TimekeepingService(db).import_csv(raw, dry_run=dry_run, actor_id=current_user.id)


@router.get("/month", summary="Everyone's month: working days, present, absent, late, early, missing leaving marks")
async def month(month: date, current_user: CurrentUser = Depends(report_perm), db=Depends(get_db)) -> list[dict]:
    return await TimekeepingService(db).month_report(month)


@router.get("/me", summary="My own month: arrivals, leavings, late marks and excused days")
async def my_month(month: date | None = None, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await TimekeepingService(db).my_month(current_user.id, month or now_local().date().replace(day=1))


@router.get("/month/{employee_id}", summary="One employee's month, day by day")
async def employee_month(employee_id: uuid.UUID, month: date, current_user: CurrentUser = Depends(report_perm), db=Depends(get_db)) -> dict:
    return await TimekeepingService(db).employee_month(employee_id, month)


@router.get("/timings", summary="The default timing, each job role's, and each employee's own")
async def timings(current_user: CurrentUser = Depends(report_perm), db=Depends(get_db)) -> dict:
    return await TimekeepingService(db).timings()


@router.put("/timings/default", status_code=204, summary="Change the default timing (administration)")
async def set_default(payload: TimingIn, current_user: CurrentUser = Depends(manage_perm), db=Depends(get_db)) -> None:
    await TimekeepingService(db).set_timing("DEFAULT", None, payload.model_dump(), actor_id=current_user.id)


@router.put("/timings/job-roles/{job_role_id}", status_code=204, summary="Set a job role's timing (administration)")
async def set_role(job_role_id: uuid.UUID, payload: TimingIn, current_user: CurrentUser = Depends(manage_perm), db=Depends(get_db)) -> None:
    await TimekeepingService(db).set_timing("JOB_ROLE", job_role_id, payload.model_dump(), actor_id=current_user.id)


@router.delete("/timings/job-roles/{job_role_id}", status_code=204, summary="Remove a job role's timing (the default applies)")
async def clear_role(job_role_id: uuid.UUID, current_user: CurrentUser = Depends(manage_perm), db=Depends(get_db)) -> None:
    await TimekeepingService(db).set_timing("JOB_ROLE", job_role_id, None, actor_id=current_user.id)


@router.put("/timings/employees/{employee_id}", status_code=204, summary="Give one employee their own timing (administration)")
async def set_emp(employee_id: uuid.UUID, payload: TimingIn, current_user: CurrentUser = Depends(manage_perm), db=Depends(get_db)) -> None:
    await TimekeepingService(db).set_timing("EMPLOYEE", employee_id, payload.model_dump(), actor_id=current_user.id)


@router.delete("/timings/employees/{employee_id}", status_code=204, summary="Remove an employee's own timing (their job role's applies)")
async def clear_emp(employee_id: uuid.UUID, current_user: CurrentUser = Depends(manage_perm), db=Depends(get_db)) -> None:
    await TimekeepingService(db).set_timing("EMPLOYEE", employee_id, None, actor_id=current_user.id)

# ---------------------------------------------------------------------------
# Leave applications (app/timekeeping/leave.py)
# ---------------------------------------------------------------------------
class LeaveApplyIn(BaseModel):
    kind: str = Field(pattern="^(CASUAL|SICK|UNPAID)$")
    first_day: date
    last_day: date
    reason: str = Field(min_length=5, max_length=500)


class LeaveDecideIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=300)
    as_kind: str | None = Field(default=None, pattern="^(CASUAL|SICK|UNPAID)$")


@router.get("/leave/me", summary="My leave applications and what is left of my casual (this month) and sick (this year) leave")
async def my_leave(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    from app.timekeeping.leave import LeaveService

    return await LeaveService(db).mine(current_user.id)


@router.post("/leave", status_code=201, summary="Apply for leave (casual, sick or unpaid); the administration decides")
async def apply_leave(payload: LeaveApplyIn, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    from app.timekeeping.leave import LeaveService

    return await LeaveService(db).apply(user_id=current_user.id, kind=payload.kind, first=payload.first_day, last=payload.last_day, reason=payload.reason)


@router.post("/leave/{request_id}/cancel", summary="Withdraw my own application while it is still waiting")
async def cancel_leave(request_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    from app.timekeeping.leave import LeaveService

    return await LeaveService(db).cancel(request_id, user_id=current_user.id)


@router.get("/leave", summary="Leave applications (the waiting ones first) -- administration")
async def list_leave(status: str | None = None, current_user: CurrentUser = Depends(manage_perm), db=Depends(get_db)) -> list[dict]:
    from app.timekeeping.leave import LeaveService

    return await LeaveService(db).listing(status=status)


@router.post("/leave/{request_id}/decide", summary="Approve (optionally as another kind) or refuse a leave application -- administration")
async def decide_leave(request_id: uuid.UUID, payload: LeaveDecideIn, current_user: CurrentUser = Depends(manage_perm), db=Depends(get_db)) -> dict:
    from app.timekeeping.leave import LeaveService

    return await LeaveService(db).decide(request_id, approve=payload.approve, note=payload.note, as_kind=payload.as_kind, actor_id=current_user.id)
