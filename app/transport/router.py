"""Transport API (app/transport/service.py)."""
from __future__ import annotations

import uuid
from datetime import date, time
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission

router = APIRouter(prefix="/transport", tags=["transport"])
manage = require_permission(Permissions.TRANSPORT_MANAGE)


def _svc(db):
    from app.transport.service import TransportService

    return TransportService(db)


@router.get("/routes", summary="Routes with their stops and riders")
async def routes(current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    return await _svc(db).routes()


class RouteIn(BaseModel):
    name: str = Field(max_length=100)
    vehicle: str | None = Field(default=None, max_length=60)
    seats: int | None = Field(default=None, ge=1, le=200)
    driver_name: str | None = Field(default=None, max_length=100)
    driver_phone: str | None = Field(default=None, max_length=30)
    monthly_fee: Decimal = Field(default=Decimal(0), ge=0, le=1_000_000)
    is_active: bool | None = None


@router.post("/routes", status_code=201, summary="Add a route")
async def add_route(payload: RouteIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await _svc(db).save_route(payload.model_dump(), route_id=None, actor_id=current_user.id)


@router.put("/routes/{route_id}", summary="Change a route")
async def edit_route(route_id: uuid.UUID, payload: RouteIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await _svc(db).save_route(payload.model_dump(), route_id=route_id, actor_id=current_user.id)


class StopIn(BaseModel):
    name: str = Field(max_length=100)
    position: int = Field(default=0, ge=0, le=200)
    pickup_time: time | None = None


@router.post("/routes/{route_id}/stops", status_code=201, summary="Add a stop")
async def add_stop(route_id: uuid.UUID, payload: StopIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await _svc(db).add_stop(route_id, **payload.model_dump())


@router.delete("/stops/{stop_id}", summary="Remove a stop (its riders keep the route)")
async def remove_stop(stop_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    await _svc(db).remove_stop(stop_id)
    return {"deleted": True}


class RiderIn(BaseModel):
    route_id: uuid.UUID
    student_id: uuid.UUID | None = None
    roll_number: str | None = Field(default=None, max_length=30)
    stop_id: uuid.UUID | None = None
    start_date: date | None = None
    monthly_fee: Decimal | None = Field(default=None, ge=0, le=1_000_000)


@router.post("/riders", status_code=201, summary="Give a student a seat on a route")
async def add_rider(payload: RiderIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    from sqlalchemy import select

    from app.core.exceptions import ResourceNotFoundError
    from app.students.models import Student

    data = payload.model_dump()
    roll = (data.pop("roll_number") or "").strip()
    if roll and not data["student_id"]:
        data["student_id"] = (await db.execute(select(Student.id).where(Student.roll_number == roll))).scalar_one_or_none()
    if not data["student_id"]:
        raise ResourceNotFoundError("Student not found.")
    return await _svc(db).add_rider(**data, actor_id=current_user.id)


class EndIn(BaseModel):
    end_date: date | None = None


@router.post("/riders/{rider_id}/end", summary="A student stops using transport")
async def end_rider(rider_id: uuid.UUID, payload: EndIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await _svc(db).end_rider(rider_id, end_date=payload.end_date, actor_id=current_user.id)
