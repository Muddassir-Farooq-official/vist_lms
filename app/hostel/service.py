from __future__ import annotations

import calendar
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.clock import institute_today
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
from app.hostel.models import HostelCharge, HostelRoom, HostelStay

GENDERS = ("MALE", "FEMALE", "ANY")


def _f(v) -> float:
    return round(float(v or 0), 2)


class HostelService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ rooms
    async def rooms(self, *, include_inactive: bool = False) -> list[dict]:
        from app.students.models import Student

        stmt = select(HostelRoom).order_by(HostelRoom.block, HostelRoom.code)
        if not include_inactive:
            stmt = stmt.where(HostelRoom.is_active.is_(True))
        rooms = list((await self.session.execute(stmt)).scalars())
        stays = (await self.session.execute(select(HostelStay, Student).join(Student, Student.id == HostelStay.student_id)
                                            .where(HostelStay.end_date.is_(None)))).all()
        by_room: dict = {}
        for st, s in stays:
            by_room.setdefault(st.room_id, []).append({"stay_id": st.id, "student_id": s.id, "name": f"{s.first_name} {s.last_name}", "roll_number": s.roll_number,
                                                       "bed_no": st.bed_no, "since": st.start_date, "monthly_fee": _f(st.monthly_fee)})
        return [{"id": r.id, "code": r.code, "block": r.block, "floor": r.floor, "capacity": r.capacity, "gender": r.gender, "monthly_fee": _f(r.monthly_fee),
                 "is_active": r.is_active, "note": r.note, "residents": by_room.get(r.id, []), "free_beds": max(0, r.capacity - len(by_room.get(r.id, [])))}
                for r in rooms]

    async def save_room(self, data: dict, *, room_id: uuid.UUID | None, actor_id: uuid.UUID) -> HostelRoom:
        data = {**data}
        if "code" in data and data["code"] is not None:
            data["code"] = data["code"].strip().upper()
        if data.get("gender") and data["gender"] not in GENDERS:
            raise BusinessRuleViolationError("Choose boys, girls or either.")
        if room_id is None:
            if (await self.session.execute(select(HostelRoom.id).where(func.upper(HostelRoom.code) == data["code"]))).first():
                raise BusinessRuleViolationError(f"Room {data['code']} already exists.")
            room = HostelRoom(**data)
            self.session.add(room)
        else:
            room = await self.session.get(HostelRoom, room_id)
            if room is None:
                raise ResourceNotFoundError("Room not found.")
            living = (await self.session.execute(select(func.count()).select_from(HostelStay).where(HostelStay.room_id == room.id, HostelStay.end_date.is_(None)))).scalar_one()
            if data.get("capacity") is not None and data["capacity"] < living:
                raise BusinessRuleViolationError(f"{living} students live in this room now; capacity cannot be less.")
            if data.get("is_active") is False and living:
                raise BusinessRuleViolationError("Move the residents out before closing the room.")
            for k, v in data.items():
                if v is not None:
                    setattr(room, k, v)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="hostel.room_saved", entity_type="hostel_room", entity_id=room.id,
                             after={k: str(v) for k, v in data.items() if v is not None})
        return room

    # ------------------------------------------------------------------ stays
    async def check_in(self, *, room_id: uuid.UUID, student_id: uuid.UUID, start_date: date | None, bed_no: str | None,
                       monthly_fee: Decimal | None, actor_id: uuid.UUID) -> HostelStay:
        from app.students.models import Student, StudentStatus

        room = await self.session.get(HostelRoom, room_id)
        if room is None or not room.is_active:
            raise BusinessRuleViolationError("Choose an open room.")
        await self.session.execute(select(HostelRoom.id).where(HostelRoom.id == room.id).with_for_update())     # two check-ins for the last bed
        s = await self.session.get(Student, student_id)
        if s is None or s.status != StudentStatus.ACTIVE:
            raise BusinessRuleViolationError("Only a current student can be given a room.")
        if room.gender != "ANY" and (s.gender or "").upper() != room.gender:
            raise BusinessRuleViolationError(f"Room {room.code} is for {'boys' if room.gender == 'MALE' else 'girls'} only.")
        if (await self.session.execute(select(HostelStay.id).where(HostelStay.student_id == s.id, HostelStay.end_date.is_(None)))).first():
            raise BusinessRuleViolationError("This student already has a room. Move them instead.")
        living = (await self.session.execute(select(func.count()).select_from(HostelStay).where(HostelStay.room_id == room.id, HostelStay.end_date.is_(None)))).scalar_one()
        if living >= room.capacity:
            raise BusinessRuleViolationError(f"Room {room.code} is full ({room.capacity}).")
        start = start_date or institute_today()
        if start > institute_today() + timedelta(days=60):
            raise BusinessRuleViolationError("The start date is too far ahead.")
        stay = HostelStay(room_id=room.id, student_id=s.id, start_date=start, bed_no=(bed_no or "").strip() or None,
                          monthly_fee=monthly_fee if monthly_fee is not None else room.monthly_fee, created_by=actor_id)
        self.session.add(stay)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="hostel.checked_in", entity_type="student", entity_id=s.id,
                             metadata={"room": room.code, "from": str(start), "monthly_fee": str(stay.monthly_fee)})
        return stay

    async def check_out(self, stay_id: uuid.UUID, *, end_date: date | None, reason: str, actor_id: uuid.UUID) -> HostelStay:
        stay = await self.session.get(HostelStay, stay_id)
        if stay is None or stay.end_date is not None:
            raise BusinessRuleViolationError("This stay has already ended.")
        end = end_date or institute_today()
        if end < stay.start_date:
            raise BusinessRuleViolationError("The leaving date is before the stay started.")
        if len((reason or "").strip()) < 3:
            raise BusinessRuleViolationError("Say why (left the hostel, moved room, graduated...).")
        stay.end_date, stay.end_reason = end, reason.strip()[:200]
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="hostel.checked_out", entity_type="student", entity_id=stay.student_id,
                             metadata={"stay": str(stay.id), "to": str(end), "reason": reason.strip()})
        return stay

    async def move(self, stay_id: uuid.UUID, *, room_id: uuid.UUID, bed_no: str | None, actor_id: uuid.UUID) -> HostelStay:
        stay = await self.session.get(HostelStay, stay_id)
        if stay is None or stay.end_date is not None:
            raise BusinessRuleViolationError("Only a current stay can be moved.")
        if stay.room_id == room_id:
            raise BusinessRuleViolationError("The student is already in that room.")
        today = institute_today()
        # the old room up to yesterday, the new one from today (a day is never charged twice)
        await self.check_out(stay.id, end_date=max(today - timedelta(days=1), stay.start_date), reason="Moved to another room", actor_id=actor_id)
        return await self.check_in(room_id=room_id, student_id=stay.student_id, start_date=max(today, stay.start_date), bed_no=bed_no,
                                   monthly_fee=None, actor_id=actor_id)

    # ------------------------------------------------------------------ the monthly charge
    async def charge_month(self, month: date, *, actor_id: uuid.UUID, dry_run: bool = False) -> dict:
        """Add each resident's hostel charge for the month to their dues. A stay that started or ended
        part-way is charged for the days it covered. Running it again charges only who was missed."""
        from app.fines.models import FinePayerType, FineSource
        from app.fines.service import FineService

        month = month.replace(day=1)
        last = month.replace(day=calendar.monthrange(month.year, month.month)[1])
        if month > institute_today().replace(day=1):
            raise BusinessRuleViolationError("A month can be charged once it has started.")
        stays = (await self.session.execute(select(HostelStay).where(HostelStay.start_date <= last,
                                                                    (HostelStay.end_date.is_(None)) | (HostelStay.end_date >= month)))).scalars().all()
        done = set((await self.session.execute(select(HostelCharge.stay_id).where(HostelCharge.month == month))).scalars())
        rooms = {r.id: r for r in (await self.session.execute(select(HostelRoom))).scalars()}
        days = (last - month).days + 1
        charged, total, skipped = 0, Decimal(0), 0
        for st in stays:
            if st.id in done:
                skipped += 1
                continue
            first, final = max(st.start_date, month), min(st.end_date or last, last)
            covered = (final - first).days + 1
            amount = (Decimal(str(st.monthly_fee)) * covered / days).quantize(Decimal("1")) if covered < days else Decimal(str(st.monthly_fee))
            if amount <= 0:
                continue
            charged += 1
            total += amount
            if dry_run:
                continue
            room = rooms.get(st.room_id)
            label = f"Hostel {month:%B %Y}, room {room.code if room else '?'}" + (f" ({covered} of {days} days)" if covered < days else "")
            fine = await FineService(self.session).issue(payer_type=FinePayerType.STUDENT, payer_id=st.student_id, amount=float(amount), reason=label,
                                                         source=FineSource.HOSTEL, actor_id=actor_id, related_entity_type="hostel_stay",
                                                         related_entity_id=st.id, notify_title="Hostel charge added to your dues")
            self.session.add(HostelCharge(stay_id=st.id, month=month, amount=amount, fine_id=fine.id, created_by=actor_id))
        if not dry_run:
            await self.session.flush()
            await self.audit.log(actor_user_id=actor_id, action="hostel.month_charged", entity_type="hostel", entity_id=None,
                                 metadata={"month": str(month), "students": charged, "total": str(total), "already_charged": skipped})
        return {"month": month, "students": charged, "total": float(total), "already_charged": skipped, "dry_run": dry_run}

    async def summary(self) -> dict:
        rooms = await self.rooms()
        beds = sum(r["capacity"] for r in rooms)
        used = sum(len(r["residents"]) for r in rooms)
        from app.fines.models import Fine, FineSource, FineStatus

        due = float((await self.session.execute(select(func.coalesce(func.sum(Fine.amount), 0)).where(
            Fine.source == FineSource.HOSTEL, Fine.status == FineStatus.PENDING))).scalar_one())
        return {"rooms": len(rooms), "beds": beds, "residents": used, "free_beds": beds - used, "charges_unpaid": round(due, 2)}

    async def student_stay(self, student_id: uuid.UUID) -> dict | None:
        st = (await self.session.execute(select(HostelStay).where(HostelStay.student_id == student_id, HostelStay.end_date.is_(None)))).scalar_one_or_none()
        if st is None:
            return None
        room = await self.session.get(HostelRoom, st.room_id)
        return {"stay_id": st.id, "room": room.code if room else None, "block": room.block if room else None, "bed_no": st.bed_no, "since": st.start_date,
                "monthly_fee": _f(st.monthly_fee)}

    async def history(self, *, q: str | None = None, limit: int = 300) -> list[dict]:
        from app.students.models import Student

        stmt = (select(HostelStay, HostelRoom.code, Student).join(HostelRoom, HostelRoom.id == HostelStay.room_id).join(Student, Student.id == HostelStay.student_id)
                .order_by(HostelStay.start_date.desc()).limit(max(1, min(limit, 2000))))
        if q and q.strip():
            n = f"%{q.strip().lower()}%"
            stmt = stmt.where(func.lower(Student.first_name + " " + Student.last_name).like(n) | func.lower(Student.roll_number).like(n) | func.lower(HostelRoom.code).like(n))
        return [{"stay_id": st.id, "room": code, "student": f"{s.first_name} {s.last_name}", "roll_number": s.roll_number, "from": st.start_date, "to": st.end_date,
                 "reason": st.end_reason, "monthly_fee": _f(st.monthly_fee)} for st, code, s in (await self.session.execute(stmt)).all()]