from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.calendar.models import KINDS, CalendarEvent
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError

MAX_DAYS = 120


async def student_holidays(session: AsyncSession, start: date, end: date) -> dict[date, str]:
    """Every day from start to end that is a holiday for students, with its title."""
    rows = (await session.execute(select(CalendarEvent).where(CalendarEvent.kind == "HOLIDAY", CalendarEvent.for_students.is_(True),
                                                              CalendarEvent.start_date <= end, CalendarEvent.end_date >= start))).scalars()
    out: dict[date, str] = {}
    for e in rows:
        d = max(e.start_date, start)
        while d <= min(e.end_date, end):
            out[d] = e.title
            d += timedelta(days=1)
    return out


def _out(e: CalendarEvent) -> dict:
    return {"id": e.id, "title": e.title, "kind": e.kind, "start_date": e.start_date, "end_date": e.end_date, "details": e.details,
            "for_students": e.for_students, "for_staff": e.for_staff, "days": (e.end_date - e.start_date).days + 1}


class CalendarService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def list(self, *, start: date, end: date, audience: str | None = None) -> list[dict]:
        stmt = select(CalendarEvent).where(CalendarEvent.start_date <= end, CalendarEvent.end_date >= start).order_by(CalendarEvent.start_date)
        if audience == "STUDENTS":
            stmt = stmt.where(CalendarEvent.for_students.is_(True))
        elif audience == "STAFF":
            stmt = stmt.where(CalendarEvent.for_staff.is_(True))
        return [_out(e) for e in (await self.session.execute(stmt)).scalars()]

    def _check(self, data: dict) -> None:
        if data["kind"] not in KINDS:
            raise BusinessRuleViolationError("Choose holiday, exam, event, term or deadline.")
        if data["end_date"] < data["start_date"]:
            raise BusinessRuleViolationError("The last day is before the first day.")
        if (data["end_date"] - data["start_date"]).days >= MAX_DAYS:
            raise BusinessRuleViolationError(f"An entry can cover at most {MAX_DAYS} days.")
        if not (data["for_students"] or data["for_staff"]):
            raise BusinessRuleViolationError("Choose who it is for: students, staff or both.")

    async def _apply_holiday(self, e: CalendarEvent, actor_id: uuid.UUID) -> dict:
        """Cancel classes (students) and excuse attendance (staff) on each day of a holiday."""
        effects = {"classes_cancelled": 0, "staff_days_excused": 0}
        d = e.start_date
        while d <= e.end_date:
            if e.for_students:
                from app.academics.service import ClassSessionService

                r = await ClassSessionService(self.session).cancel_day(d, reason=f"Holiday: {e.title}", term_label=None, actor_id=actor_id)
                effects["classes_cancelled"] += r["cancelled"]
            if e.for_staff:
                from app.timekeeping.service import TimekeepingService

                effects["staff_days_excused"] += await TimekeepingService(self.session).holiday(d, f"Holiday: {e.title}", actor_id=actor_id)
            d += timedelta(days=1)
        return effects

    async def create(self, data: dict, *, actor_id: uuid.UUID) -> dict:
        data = {**data, "title": data["title"].strip(), "details": (data.get("details") or "").strip() or None}
        self._check(data)
        e = CalendarEvent(**data, created_by=actor_id)
        self.session.add(e)
        await self.session.flush()
        effects = await self._apply_holiday(e, actor_id) if e.kind == "HOLIDAY" else {}
        await self.audit.log(actor_user_id=actor_id, action="calendar.event_added", entity_type="calendar_event", entity_id=e.id,
                             after={k: str(v) for k, v in data.items()}, metadata=effects or None)
        if e.kind in ("HOLIDAY", "EXAM", "DEADLINE"):
            await self._notify(e)
        return {**_out(e), "effects": effects}

    async def _notify(self, e: CalendarEvent) -> None:
        """Holidays, exams and deadlines appear in everyone's notifications (no SMS: use the SMS page for that)."""
        from app.auth.models import Role, User, UserRole
        from app.notifications.models import NotificationCategory
        from app.notifications.service import create_notification

        roles = (["STUDENT", "PARENT"] if e.for_students else []) + (["TEACHER", "ADMIN", "ACCOUNTS_OFFICER", "LIBRARIAN", "SECURITY_GUARD",
                                                                     "EXAM_CONTROLLER", "WARDEN", "PRINCIPAL", "HOD"] if e.for_staff else [])
        users = set((await self.session.execute(select(User.id).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id)
                                                .where(Role.code.in_(roles), User.is_active.is_(True)))).scalars())
        when = e.start_date.strftime("%d %b %Y") + ("" if e.end_date == e.start_date else " to " + e.end_date.strftime("%d %b %Y"))
        for uid in users:
            await create_notification(self.session, user_id=uid, category=NotificationCategory.ANNOUNCEMENT, title=f"{e.kind.title()}: {e.title}",
                                      message=when + (f". {e.details}" if e.details else ""), link=None)

    async def delete(self, event_id: uuid.UUID, *, actor_id: uuid.UUID) -> None:
        e = await self.session.get(CalendarEvent, event_id)
        if e is None:
            raise ResourceNotFoundError("Calendar entry not found.")
        before = _out(e)
        await self.session.delete(e)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="calendar.event_removed", entity_type="calendar_event", entity_id=event_id,
                             before={k: str(v) for k, v in before.items()},
                             metadata={"note": "Classes already cancelled and staff days already excused stay as they are; restore them one by one if needed."})