"""Transport routes, stops and riders; the month's transport fee lines for the draft (app/monthly_charges)."""
from __future__ import annotations

import calendar
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.clock import institute_today
from app.core.exceptions import BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError, ValidationAppError
from app.transport.models import TransportRider, TransportRoute, TransportStop


def _f(v) -> float:
    return round(float(v or 0), 2)


class TransportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def routes(self) -> list[dict]:
        from app.students.models import Student

        routes = (await self.session.execute(select(TransportRoute).order_by(TransportRoute.name))).scalars().all()
        stops = (await self.session.execute(select(TransportStop).order_by(TransportStop.position, TransportStop.name))).scalars().all()
        riders = (await self.session.execute(select(TransportRider, Student).join(Student, Student.id == TransportRider.student_id)
                                             .where(TransportRider.end_date.is_(None)).order_by(Student.roll_number))).all()
        names = {s.id: s.name for s in stops}
        by_route, rid = {}, {}
        for s in stops:
            by_route.setdefault(s.route_id, []).append({"id": s.id, "name": s.name, "position": s.position, "pickup_time": s.pickup_time.strftime("%H:%M") if s.pickup_time else None})
        for r, s in riders:
            rid.setdefault(r.route_id, []).append({"rider_id": r.id, "student_id": s.id, "name": f"{s.first_name} {s.last_name}", "roll_number": s.roll_number,
                                                   "stop": names.get(r.stop_id), "since": r.start_date, "monthly_fee": _f(r.monthly_fee)})
        return [{"id": r.id, "name": r.name, "vehicle": r.vehicle, "seats": r.seats, "driver_name": r.driver_name, "driver_phone": r.driver_phone,
                 "monthly_fee": _f(r.monthly_fee), "is_active": r.is_active, "stops": by_route.get(r.id, []), "riders": rid.get(r.id, [])} for r in routes]

    async def save_route(self, data: dict, *, route_id: uuid.UUID | None, actor_id: uuid.UUID) -> dict:
        name = " ".join((data.get("name") or "").split())
        if len(name) < 2:
            raise ValidationAppError("Give the route a name.")
        clash = (await self.session.execute(select(TransportRoute).where(func.lower(TransportRoute.name) == name.lower()))).scalar_one_or_none()
        if clash and clash.id != route_id:
            raise DuplicateResourceError("A route with that name exists.")
        r = await self.session.get(TransportRoute, route_id) if route_id else None
        if route_id and r is None:
            raise ResourceNotFoundError("Route not found.")
        if r is None:
            r = TransportRoute(name=name, monthly_fee=data.get("monthly_fee") or 0)
            self.session.add(r)
        if data.get("is_active") is False and r.id and (await self.session.execute(select(TransportRider.id).where(TransportRider.route_id == r.id,
                                                                                                                   TransportRider.end_date.is_(None)))).first():
            raise BusinessRuleViolationError("Take the riders off this route before closing it.")
        for k in ("vehicle", "seats", "driver_name", "driver_phone", "monthly_fee", "is_active"):
            if k in data and data[k] is not None:
                setattr(r, k, data[k])
        r.name = name
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="transport.route_saved", entity_type="transport_route", entity_id=r.id,
                                             after={k: str(v) for k, v in data.items() if v is not None})
        return {"id": r.id}

    async def add_stop(self, route_id: uuid.UUID, *, name: str, position: int, pickup_time) -> dict:
        if await self.session.get(TransportRoute, route_id) is None:
            raise ResourceNotFoundError("Route not found.")
        name = " ".join((name or "").split())
        if len(name) < 2:
            raise ValidationAppError("Give the stop a name.")
        if (await self.session.execute(select(TransportStop.id).where(TransportStop.route_id == route_id, func.lower(TransportStop.name) == name.lower()))).first():
            raise DuplicateResourceError("This route already has that stop.")
        s = TransportStop(route_id=route_id, name=name, position=position, pickup_time=pickup_time)
        self.session.add(s)
        await self.session.flush()
        return {"id": s.id}

    async def remove_stop(self, stop_id: uuid.UUID) -> None:
        s = await self.session.get(TransportStop, stop_id)
        if s is None:
            raise ResourceNotFoundError("Stop not found.")
        await self.session.delete(s)                 # riders keep their route; their stop is cleared
        await self.session.flush()

    async def add_rider(self, *, route_id: uuid.UUID, student_id: uuid.UUID, stop_id: uuid.UUID | None, start_date: date | None,
                        monthly_fee: Decimal | None, actor_id: uuid.UUID) -> dict:
        from app.students.models import Student, StudentStatus

        r = await self.session.get(TransportRoute, route_id)
        if r is None or not r.is_active:
            raise BusinessRuleViolationError("Choose an active route.")
        await self.session.execute(select(TransportRoute.id).where(TransportRoute.id == r.id).with_for_update())
        s = await self.session.get(Student, student_id)
        if s is None or s.status != StudentStatus.ACTIVE:
            raise BusinessRuleViolationError("Only a current student can be given a seat.")
        if (await self.session.execute(select(TransportRider.id).where(TransportRider.student_id == s.id, TransportRider.end_date.is_(None)))).first():
            raise BusinessRuleViolationError("This student already rides a route. End that first.")
        if stop_id:
            stop = await self.session.get(TransportStop, stop_id)
            if stop is None or stop.route_id != r.id:
                raise ValidationAppError("That stop is not on this route.")
        if r.seats:
            riding = (await self.session.execute(select(func.count()).select_from(TransportRider).where(TransportRider.route_id == r.id,
                                                                                                       TransportRider.end_date.is_(None)))).scalar_one()
            if riding >= r.seats:
                raise BusinessRuleViolationError(f"{r.name} is full ({r.seats} seats).")
        start = start_date or institute_today()
        if start > institute_today() + timedelta(days=60):
            raise BusinessRuleViolationError("The start date is too far ahead.")
        x = TransportRider(route_id=r.id, stop_id=stop_id, student_id=s.id, start_date=start, monthly_fee=monthly_fee if monthly_fee is not None else r.monthly_fee,
                           created_by=actor_id)
        self.session.add(x)
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="transport.rider_added", entity_type="student", entity_id=s.id,
                                             metadata={"route": r.name, "from": str(start), "monthly_fee": str(x.monthly_fee)})
        return {"id": x.id}

    async def end_rider(self, rider_id: uuid.UUID, *, end_date: date | None, actor_id: uuid.UUID) -> dict:
        x = await self.session.get(TransportRider, rider_id)
        if x is None or x.end_date is not None:
            raise BusinessRuleViolationError("This seat has already ended.")
        end = end_date or institute_today()
        if end < x.start_date:
            raise BusinessRuleViolationError("The last day is before the seat started.")
        x.end_date = end
        await self.session.flush()
        await AuditService(self.session).log(actor_user_id=actor_id, action="transport.rider_ended", entity_type="student", entity_id=x.student_id,
                                             metadata={"rider": str(x.id), "to": str(end)})
        return {"id": x.id, "end_date": end}

    async def month_lines(self, month: date) -> list[dict]:
        """Each rider's fee for the month; a seat that started or ended part-way pays for the days it covered.
        A seat already charged for the month is left out."""
        from app.monthly_charges.models import ChargeLine

        month = month.replace(day=1)
        if month > institute_today().replace(day=1):
            raise BusinessRuleViolationError("A month can be charged once it has started.")
        last = month.replace(day=calendar.monthrange(month.year, month.month)[1])
        days = (last - month).days + 1
        done = set((await self.session.execute(select(ChargeLine.ref_id).where(ChargeLine.kind == "TRANSPORT", ChargeLine.month == month,
                                                                               ChargeLine.fine_id.is_not(None)))).scalars())
        rows = (await self.session.execute(select(TransportRider, TransportRoute).join(TransportRoute, TransportRoute.id == TransportRider.route_id).where(
            TransportRider.start_date <= last, (TransportRider.end_date.is_(None)) | (TransportRider.end_date >= month)))).all()
        out = []
        for x, r in rows:
            if x.id in done:
                continue
            first, final = max(x.start_date, month), min(x.end_date or last, last)
            covered = (final - first).days + 1
            fee = Decimal(str(x.monthly_fee))
            amount = fee if covered >= days else (fee * covered / days).quantize(Decimal("1"))
            if amount <= 0:
                continue
            out.append({"kind": "TRANSPORT", "ref_id": x.id, "student_id": x.student_id, "amount": amount, "days": covered,
                        "label": f"Transport {month:%B %Y}, {r.name}" + (f" ({covered} of {days} days)" if covered < days else "")})
        return out

    async def student_seat(self, student_id: uuid.UUID) -> dict | None:
        row = (await self.session.execute(select(TransportRider, TransportRoute).join(TransportRoute, TransportRoute.id == TransportRider.route_id)
                                          .where(TransportRider.student_id == student_id, TransportRider.end_date.is_(None)))).first()
        if row is None:
            return None
        x, r = row
        stop = await self.session.get(TransportStop, x.stop_id) if x.stop_id else None
        return {"route": r.name, "vehicle": r.vehicle, "driver_name": r.driver_name, "driver_phone": r.driver_phone, "stop": stop.name if stop else None,
                "pickup_time": stop.pickup_time.strftime("%H:%M") if stop and stop.pickup_time else None, "monthly_fee": _f(x.monthly_fee)}
