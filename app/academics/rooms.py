"""Rooms: the institute's classrooms, labs and halls, with how many seats each has.

Until the first room is added, every room typed on the timetable is accepted (as before). Once rooms are listed here:
  * a weekly timetable slot must use a listed, active room, and one with enough seats for the students enrolled;
  * the free-slot finder (make-up / extra classes) offers only listed rooms with enough seats.
Rooms with no seat count are treated as big enough.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Integer, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.audit.service import AuditService
from app.auth.permissions import Permissions
from app.core.database import Base, TimestampMixin, UUIDPKMixin, get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission, require_any_permission
from app.core.exceptions import BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError

KINDS = {"CLASS": "Classroom", "LAB": "Lab", "HALL": "Hall"}


def room_key(name: str | None) -> str:
    """Spaces and case are ignored, so 'Lab 1' and 'lab1' are the same room (as on the timetable)."""
    return "".join((name or "").split()).lower()


class Room(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "rooms"

    name: Mapped[str] = mapped_column(String(50), nullable=False)
    key: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)       # room_key(name)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="CLASS")
    seats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Exam seating (app/exams/seating.py): the room's exam layout, rows x columns of seats; empty = not an exam room.
    exam_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exam_cols: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


def _audit(r: Room) -> dict:
    return {"name": r.name, "kind": r.kind, "seats": r.seats, "is_active": r.is_active}


def to_dict(r: Room) -> dict:
    return {"id": r.id, "name": r.name, "kind": r.kind, "kind_label": KINDS.get(r.kind, r.kind), "seats": r.seats, "is_active": r.is_active}


class RoomService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def listing(self, *, active_only: bool = False) -> list[Room]:
        stmt = select(Room).order_by(func.lower(Room.name))
        if active_only:
            stmt = stmt.where(Room.is_active.is_(True))
        return list((await self.session.execute(stmt)).scalars().all())

    async def any_listed(self) -> bool:
        return (await self.session.execute(select(func.count()).select_from(Room))).scalar_one() > 0

    async def check_for_class(self, room: str, enrolled: int) -> None:
        """Refuse a timetable room that is not listed (once rooms are listed) or too small for the class."""
        if not await self.any_listed():
            return
        r = (await self.session.execute(select(Room).where(Room.key == room_key(room)))).scalar_one_or_none()
        if r is None or not r.is_active:
            raise BusinessRuleViolationError(f"Room '{room}' is not on the room list. Add it on Timetable → Rooms, or pick a listed room.")
        if r.seats is not None and enrolled > r.seats:
            raise BusinessRuleViolationError(f"Room {r.name} has {r.seats} seats but {enrolled} students are enrolled. Pick a bigger room.")

    async def create(self, *, name: str, kind: str, seats: int | None, actor_id) -> Room:
        name = " ".join(name.split())
        if not name:
            raise BusinessRuleViolationError("Give the room a name.")
        if (await self.session.execute(select(Room).where(Room.key == room_key(name)))).scalar_one_or_none():
            raise DuplicateResourceError(f"Room {name} is already on the list.")
        r = Room(name=name, key=room_key(name), kind=kind, seats=seats, is_active=True)
        self.session.add(r)
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="room.created", entity_type="room", entity_id=r.id, after=_audit(r))
        return r

    async def update(self, room_id: uuid.UUID, *, kind: str | None, seats: int | None, seats_given: bool, is_active: bool | None, actor_id) -> Room:
        r = await self.session.get(Room, room_id)
        if r is None:
            raise ResourceNotFoundError("Room not found.")
        before = _audit(r)
        if kind is not None:
            r.kind = kind
        if seats_given:
            r.seats = seats
        if is_active is not None:
            r.is_active = is_active
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="room.updated", entity_type="room", entity_id=r.id,
                                             before=before, after=_audit(r))
        return r


router = APIRouter(prefix="/academics/rooms", tags=["rooms"])
manage = require_any_permission(Permissions.TIMETABLE_MANAGE, Permissions.COURSE_OFFERING_MANAGE)


class RoomIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    kind: str = Field(default="CLASS", pattern="^(CLASS|LAB|HALL)$")
    seats: int | None = Field(default=None, ge=1, le=5000)


class RoomPatch(BaseModel):
    kind: str | None = Field(default=None, pattern="^(CLASS|LAB|HALL)$")
    seats: int | None = Field(default=None, ge=1, le=5000)
    is_active: bool | None = None


@router.get("", summary="The room list (classrooms, labs, halls) with seats")
async def list_rooms(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return [to_dict(r) for r in await RoomService(db).listing()]


@router.post("", status_code=201, summary="Add a room")
async def add_room(payload: RoomIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return to_dict(await RoomService(db).create(name=payload.name, kind=payload.kind, seats=payload.seats, actor_id=current_user.id))


@router.patch("/{room_id}", summary="Change a room's type, seats, or take it out of use")
async def edit_room(room_id: uuid.UUID, payload: RoomPatch, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return to_dict(await RoomService(db).update(room_id, kind=payload.kind, seats=payload.seats, seats_given="seats" in payload.model_fields_set,
                                                is_active=payload.is_active, actor_id=current_user.id))
