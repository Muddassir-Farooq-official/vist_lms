"""Visitor log book: enter, leave, search, edit (administration) and export."""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.auth.models import User
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
from app.gate.models import VisitorEntry
from app.timekeeping.service import local, now_local, tz

GUARD_HISTORY_DAYS = 7


def visitor_ref(v: VisitorEntry) -> str:
    return f"VIS-{v.serial_no:06d}"


def clean_cnic(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return None
    if len(digits) != 13:
        raise BusinessRuleViolationError("CNIC must have 13 digits (e.g. 35202-1234567-1).")
    return digits


def show_cnic(digits: str | None) -> str | None:
    return f"{digits[:5]}-{digits[5:12]}-{digits[12]}" if digits and len(digits) == 13 else digits


def _clip(value, n: int) -> str | None:
    v = " ".join(str(value or "").split())
    return v[:n] or None


class GateService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def _get(self, entry_id: uuid.UUID) -> VisitorEntry:
        v = await self.session.get(VisitorEntry, entry_id)
        if v is None:
            raise ResourceNotFoundError("Visitor entry not found.")
        return v

    async def _names(self, ids: set) -> dict:
        ids = {i for i in ids if i}
        if not ids:
            return {}
        return {u.id: u.username for u in (await self.session.execute(select(User).where(User.id.in_(ids)))).scalars()}

    async def _out(self, rows: list[VisitorEntry]) -> list[dict]:
        names = await self._names({r.in_by for r in rows} | {r.out_by for r in rows})
        out = []
        for v in rows:
            ein, eout = local(v.entry_at), local(v.exit_at)
            stay = int(((v.exit_at or datetime.now(timezone.utc)) - v.entry_at).total_seconds() // 60)
            out.append({"id": v.id, "reference": visitor_ref(v), "visitor_name": v.visitor_name, "cnic": show_cnic(v.cnic), "phone": v.phone,
                        "coming_from": v.coming_from, "purpose": v.purpose, "person_to_meet": v.person_to_meet, "vehicle_no": v.vehicle_no,
                        "people_count": v.people_count, "card_no": v.card_no, "entry_at": v.entry_at, "exit_at": v.exit_at,
                        "entry_time": ein.strftime("%H:%M"), "entry_date": ein.date(), "exit_time": eout.strftime("%H:%M") if eout else None,
                        "inside": v.exit_at is None, "minutes_inside": max(0, stay), "in_by": names.get(v.in_by), "out_by": names.get(v.out_by), "note": v.note})
        return out

    def _fields(self, data: dict) -> dict:
        name = _clip(data.get("visitor_name"), 150)
        purpose = _clip(data.get("purpose"), 200)
        if not name or len(name) < 2:
            raise BusinessRuleViolationError("Enter the visitor's name.")
        if not purpose:
            raise BusinessRuleViolationError("Enter the purpose of the visit.")
        count = int(data.get("people_count") or 1)
        if not 1 <= count <= 200:
            raise BusinessRuleViolationError("Number of people must be between 1 and 200.")
        phone = re.sub(r"[^\d+]", "", data.get("phone") or "")[:20] or None
        return {"visitor_name": name, "cnic": clean_cnic(data.get("cnic")), "phone": phone, "coming_from": _clip(data.get("coming_from"), 150),
                "purpose": purpose, "person_to_meet": _clip(data.get("person_to_meet"), 150), "vehicle_no": _clip(data.get("vehicle_no"), 30),
                "people_count": count, "card_no": _clip(data.get("card_no"), 20), "note": _clip(data.get("note"), 300)}

    async def enter(self, data: dict, *, actor_id: uuid.UUID) -> dict:
        fields = self._fields(data)
        if fields["cnic"]:
            inside = (await self.session.execute(select(VisitorEntry).where(VisitorEntry.cnic == fields["cnic"], VisitorEntry.exit_at.is_(None)))).scalars().first()
            if inside is not None:
                raise BusinessRuleViolationError(f"{inside.visitor_name} (this CNIC) is already inside since {local(inside.entry_at):%d %b %H:%M}. Mark them out first.")
        v = VisitorEntry(**fields, entry_at=datetime.now(timezone.utc), in_by=actor_id)
        self.session.add(v)
        await self.session.flush()
        await self.session.refresh(v)
        await self.audit.log(actor_user_id=actor_id, action="gate.visitor_in", entity_type="visitor_entry", entity_id=v.id,
                             metadata={"name": v.visitor_name, "cnic": v.cnic, "purpose": v.purpose})
        return (await self._out([v]))[0]

    async def leave(self, entry_id: uuid.UUID, *, actor_id: uuid.UUID) -> dict:
        v = await self._get(entry_id)
        if v.exit_at is not None:
            raise BusinessRuleViolationError(f"Already marked out at {local(v.exit_at):%H:%M}.")
        v.exit_at, v.out_by = datetime.now(timezone.utc), actor_id
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="gate.visitor_out", entity_type="visitor_entry", entity_id=v.id)
        return (await self._out([v]))[0]

    async def edit(self, entry_id: uuid.UUID, data: dict, *, actor_id: uuid.UUID) -> dict:
        """Administration: correct the details or the times of an entry."""
        v = await self._get(entry_id)
        before = (await self._out([v]))[0]
        merged = {k: getattr(v, k) for k in ("visitor_name", "cnic", "phone", "coming_from", "purpose", "person_to_meet", "vehicle_no", "people_count", "card_no", "note")}
        merged.update({k: val for k, val in data.items() if k in merged})
        for k, val in self._fields(merged).items():
            setattr(v, k, val)
        day = local(v.entry_at).date()
        if data.get("entry_time"):
            v.entry_at = datetime.combine(day, data["entry_time"], tzinfo=tz()).astimezone(timezone.utc)
        if "exit_time" in data:
            v.exit_at = datetime.combine(day, data["exit_time"], tzinfo=tz()).astimezone(timezone.utc) if data["exit_time"] else None
        if v.exit_at is not None and v.exit_at < v.entry_at:
            raise BusinessRuleViolationError("The leaving time is before the arrival time.")
        await self.session.flush()
        after = (await self._out([v]))[0]
        await self.audit.log(actor_user_id=actor_id, action="gate.visitor_edited", entity_type="visitor_entry", entity_id=v.id,
                             before={k: str(before[k]) for k in ("visitor_name", "cnic", "purpose", "entry_time", "exit_time")},
                             after={k: str(after[k]) for k in ("visitor_name", "cnic", "purpose", "entry_time", "exit_time")})
        return after

    async def inside(self) -> list[dict]:
        rows = (await self.session.execute(select(VisitorEntry).where(VisitorEntry.exit_at.is_(None)).order_by(VisitorEntry.entry_at.desc()).limit(500))).scalars().all()
        return await self._out(list(rows))

    async def search(self, *, q: str | None, from_date: date | None, to_date: date | None, inside_only: bool, limit: int, offset: int, admin: bool) -> list[dict]:
        today = now_local().date()
        if not admin:                                  # a guard sees the last week only
            earliest = today - timedelta(days=GUARD_HISTORY_DAYS)
            from_date = max(from_date or earliest, earliest)
        stmt = select(VisitorEntry).order_by(VisitorEntry.entry_at.desc())
        if from_date:
            stmt = stmt.where(VisitorEntry.entry_at >= datetime.combine(from_date, time(0), tzinfo=tz()))
        if to_date:
            stmt = stmt.where(VisitorEntry.entry_at < datetime.combine(to_date + timedelta(days=1), time(0), tzinfo=tz()))
        if inside_only:
            stmt = stmt.where(VisitorEntry.exit_at.is_(None))
        if q and q.strip():
            n = q.strip().lower()
            digits = re.sub(r"\D", "", n)
            conds = [func.lower(VisitorEntry.visitor_name).like(f"%{n}%"), func.lower(VisitorEntry.purpose).like(f"%{n}%"),
                     func.lower(VisitorEntry.person_to_meet).like(f"%{n}%"), func.lower(VisitorEntry.vehicle_no).like(f"%{n}%")]
            if len(digits) >= 3:
                conds += [VisitorEntry.cnic.like(f"%{digits}%"), VisitorEntry.phone.like(f"%{digits}%")]
            stmt = stmt.where(or_(*conds))
        rows = (await self.session.execute(stmt.offset(max(0, offset)).limit(max(1, min(limit, 1000))))).scalars().all()
        return await self._out(list(rows))

    async def last_visit(self, cnic: str) -> dict | None:
        """The visitor's details from their last visit (to fill the form for a returning visitor)."""
        digits = clean_cnic(cnic)
        v = (await self.session.execute(select(VisitorEntry).where(VisitorEntry.cnic == digits).order_by(VisitorEntry.entry_at.desc()))).scalars().first()
        if v is None:
            return None
        visits = (await self.session.execute(select(func.count()).select_from(VisitorEntry).where(VisitorEntry.cnic == digits))).scalar_one()
        return {**(await self._out([v]))[0], "visits": visits}

    async def summary(self) -> dict:
        today = now_local().date()
        start = datetime.combine(today, time(0), tzinfo=tz())
        today_count = (await self.session.execute(select(func.count()).select_from(VisitorEntry).where(VisitorEntry.entry_at >= start))).scalar_one()
        people = (await self.session.execute(select(func.coalesce(func.sum(VisitorEntry.people_count), 0)).where(VisitorEntry.entry_at >= start))).scalar_one()
        inside = (await self.session.execute(select(func.count()).select_from(VisitorEntry).where(VisitorEntry.exit_at.is_(None)))).scalar_one()
        overnight = (await self.session.execute(select(func.count()).select_from(VisitorEntry).where(VisitorEntry.exit_at.is_(None), VisitorEntry.entry_at < start))).scalar_one()
        return {"today": today, "entries_today": today_count, "people_today": int(people), "inside_now": inside, "inside_since_before_today": overnight}

    async def export_csv(self, *, q: str | None, from_date: date | None, to_date: date | None) -> str:
        rows = await self.search(q=q, from_date=from_date, to_date=to_date, inside_only=False, limit=1000, offset=0, admin=True)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Ref", "Date", "In", "Out", "Name", "CNIC", "Phone", "From", "Purpose", "Meeting", "Vehicle", "People", "Card", "Entered by", "Note"])
        for r in rows:
            w.writerow([r["reference"], r["entry_date"], r["entry_time"], r["exit_time"] or "", r["visitor_name"], r["cnic"] or "", r["phone"] or "",
                        r["coming_from"] or "", r["purpose"], r["person_to_meet"] or "", r["vehicle_no"] or "", r["people_count"], r["card_no"] or "", r["in_by"] or "", r["note"] or ""])
        return buf.getvalue()