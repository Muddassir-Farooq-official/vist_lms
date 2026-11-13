"""School mode, stage 4 API: class fee plans, transport routes, monthly vouchers (accounts office)."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_any_permission, require_permission
from app.school.fees import SchoolFeeService

router = APIRouter(prefix="/school/fees", tags=["school"])
manage = require_permission(Permissions.FEE_MANAGE)
view = require_any_permission(Permissions.FEE_MANAGE, Permissions.PAYMENT_RECORD)      # the administration and the accounts office (not students)


class PlanIn(BaseModel):
    program_id: uuid.UUID
    monthly_tuition: float = Field(ge=0, le=10_000_000)
    admission_fee: float = Field(ge=0, le=10_000_000)
    annual_charges: float = Field(ge=0, le=10_000_000)


class PlansIn(BaseModel):
    session_label: str | None = None
    plans: list[PlanIn] = Field(min_length=1, max_length=100)


class RouteIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    monthly_fee: float = Field(ge=0, le=1_000_000)
    active: bool = True


class RidersIn(BaseModel):
    student_ids: list[uuid.UUID] = Field(min_length=1, max_length=300)
    from_month: date | None = None


class EndRideIn(BaseModel):
    last_month: date | None = None


class MonthIn(BaseModel):
    month: date | None = None


@router.get("/plans", summary="Each class's monthly tuition, admission fee and annual charges for a session")
async def plans(session_label: str | None = None, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    return await SchoolFeeService(db).plans(session_label)


@router.put("/plans", summary="Change classes' fees (counts for vouchers made from now on)")
async def save_plans(payload: PlansIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SchoolFeeService(db).save_plans(payload.session_label, [p.model_dump() for p in payload.plans], current_user.id)


@router.get("/routes", summary="Van / bus routes with their monthly fee")
async def routes(current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[dict]:
    return await SchoolFeeService(db).routes()


@router.post("/routes", status_code=201, summary="Add a route")
async def add_route(payload: RouteIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SchoolFeeService(db).save_route(None, name=payload.name, monthly_fee=payload.monthly_fee, active=payload.active, actor_id=current_user.id)


@router.put("/routes/{route_id}", summary="Change a route (name, monthly fee, on/off); counts for vouchers made from now on")
async def change_route(route_id: uuid.UUID, payload: RouteIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SchoolFeeService(db).save_route(route_id, name=payload.name, monthly_fee=payload.monthly_fee, active=payload.active, actor_id=current_user.id)


@router.get("/routes/{route_id}/students", summary="Students on a route")
async def riders(route_id: uuid.UUID, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[dict]:
    return await SchoolFeeService(db).riders(route_id)


@router.post("/routes/{route_id}/students", summary="Put students on a route from a month (a student on another route moves)")
async def add_riders(route_id: uuid.UUID, payload: RidersIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SchoolFeeService(db).add_riders(route_id, payload.student_ids, payload.from_month, current_user.id)


@router.post("/rides/{ride_id}/end", summary="Take a student off their route after a month")
async def end_ride(ride_id: uuid.UUID, payload: EndRideIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await SchoolFeeService(db).end_ride(ride_id, payload.last_month, current_user.id)


@router.get("/month", summary="A month's vouchers at a glance")
async def month(month: date | None = None, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    return await SchoolFeeService(db).month_view(month)


@router.post("/month", summary="Make the month's vouchers now for everyone who has none yet (otherwise made on the 1st)")
async def make_month(payload: MonthIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    svc = SchoolFeeService(db)
    res = await svc.make_month(payload.month, actor_id=current_user.id)
    return {**res, "view": await svc.month_view(payload.month)}
