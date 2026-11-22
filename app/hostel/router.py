from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.clock import institute_today
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.hostel import models as _models  # noqa: F401  (registers the tables)
from app.hostel.service import HostelService

router = APIRouter(prefix="/hostel", tags=["hostel"])
view = require_permission(Permissions.HOSTEL_VIEW)
manage = require_permission(Permissions.HOSTEL_MANAGE)


class RoomIn(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    block: str | None = Field(default=None, max_length=60)
    floor: str | None = Field(default=None, max_length=20)
    capacity: int = Field(ge=1, le=50)
    gender: str = Field(default="ANY", pattern="^(MALE|FEMALE|ANY)$")
    monthly_fee: Decimal = Field(ge=0, le=1_000_000)
    note: str | None = Field(default=None, max_length=200)


class RoomUpdate(BaseModel):
    block: str | None = Field(default=None, max_length=60)
    floor: str | None = Field(default=None, max_length=20)
    capacity: int | None = Field(default=None, ge=1, le=50)
    gender: str | None = Field(default=None, pattern="^(MALE|FEMALE|ANY)$")
    monthly_fee: Decimal | None = Field(default=None, ge=0, le=1_000_000)
    is_active: bool | None = None
    note: str | None = Field(default=None, max_length=200)


class CheckInIn(BaseModel):
    room_id: uuid.UUID
    student_id: uuid.UUID
    start_date: date | None = None
    bed_no: str | None = Field(default=None, max_length=10)
    monthly_fee: Decimal | None = Field(default=None, ge=0, le=1_000_000)
    mess_fee: Decimal | None = Field(default=None, ge=0, le=1_000_000)          # none: the default mess fee (Settings)


class CheckOutIn(BaseModel):
    end_date: date | None = None
    reason: str = Field(min_length=3, max_length=200)


class MoveIn(BaseModel):
    room_id: uuid.UUID
    bed_no: str | None = Field(default=None, max_length=10)


class ChargeIn(BaseModel):
    month: date
    dry_run: bool = False


@router.get("/summary", summary="Rooms, beds, residents and unpaid hostel charges")
async def summary(current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    return await HostelService(db).summary()


@router.get("/rooms", summary="Every room with who lives in it")
async def rooms(include_inactive: bool = False, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[dict]:
    return await HostelService(db).rooms(include_inactive=include_inactive)


@router.post("/rooms", status_code=201, summary="Add a room")
async def add_room(payload: RoomIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    r = await HostelService(db).save_room(payload.model_dump(), room_id=None, actor_id=current_user.id)
    return {"id": r.id}


@router.patch("/rooms/{room_id}", summary="Change a room (capacity, charge, open / closed)")
async def edit_room(room_id: uuid.UUID, payload: RoomUpdate, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    r = await HostelService(db).save_room(payload.model_dump(), room_id=room_id, actor_id=current_user.id)
    return {"id": r.id}


@router.post("/stays", status_code=201, summary="Give a student a room")
async def check_in(payload: CheckInIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    st = await HostelService(db).check_in(**payload.model_dump(), actor_id=current_user.id)
    return {"id": st.id}


@router.post("/stays/{stay_id}/out", summary="A student leaves the hostel")
async def check_out(stay_id: uuid.UUID, payload: CheckOutIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    st = await HostelService(db).check_out(stay_id, end_date=payload.end_date, reason=payload.reason, actor_id=current_user.id)
    credit = getattr(st, "credit_result", None) or {}
    return {"id": st.id, "end_date": st.end_date, "credited": credit.get("credited", 0.0), "refund_due": credit.get("refund_due", 0.0)}


@router.post("/stays/{stay_id}/move", summary="Move a student to another room")
async def move(stay_id: uuid.UUID, payload: MoveIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    st = await HostelService(db).move(stay_id, room_id=payload.room_id, bed_no=payload.bed_no, actor_id=current_user.id)
    return {"id": st.id}


@router.post("/charges", summary="Count the month's hostel rent (dry_run=true); charging it straight away needs the accounts approval right")
async def charge(payload: ChargeIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    from app.core.exceptions import AuthorizationError

    if not payload.dry_run and Permissions.CHARGES_APPROVE not in current_user.permissions:
        raise AuthorizationError("Prepare the month's draft; the accounts office approves it (Monthly charges).")
    return await HostelService(db).charge_month(payload.month, actor_id=current_user.id, dry_run=payload.dry_run)


class MessIn(BaseModel):
    mess_fee: Decimal = Field(ge=0, le=1_000_000)


@router.post("/stays/{stay_id}/mess", summary="Join or leave the mess (0 = not in the mess)")
async def set_mess(stay_id: uuid.UUID, payload: MessIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    st = await HostelService(db).set_mess(stay_id, payload.mess_fee, actor_id=current_user.id)
    return {"id": st.id, "mess_fee": float(st.mess_fee)}


@router.get("/roll-call", summary="The night roll call: every resident that night, with the marks saved")
async def roll_call(day: date | None = None, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await HostelService(db).roll_call(day or institute_today())


class RollIn(BaseModel):
    day: date
    entries: list[dict] = Field(max_length=2000)


@router.post("/roll-call", summary="Save the night roll call (parents are told about an absent resident, once)")
async def mark_roll_call(payload: RollIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await HostelService(db).mark_roll_call(payload.day, payload.entries, actor_id=current_user.id)


@router.get("/history", summary="Who lived where, newest first")
async def history(q: str | None = None, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[dict]:
    return await HostelService(db).history(q=q)


@router.get("/students/{student_id}", summary="The student's current room, if any")
async def student_stay(student_id: uuid.UUID, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    return {"stay": await HostelService(db).student_stay(student_id), "today": institute_today()}