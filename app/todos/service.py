"""Daily to-do lists (see models.py for the rules)."""
from __future__ import annotations

import csv
import io
import uuid
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import institute_now, institute_today
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError
from app.todos.models import TodoItem, TodoStatus

MAX_PER_DAY = 50
PLAN_AHEAD_DAYS = 31
STATUS_LABEL = {"TODO": "To do", "IN_PROGRESS": "In progress", "DONE": "Done", "NOT_DONE": "Not done", "CARRIED_OVER": "Carried over"}
SETTABLE = {TodoStatus.TODO.value, TodoStatus.IN_PROGRESS.value, TodoStatus.DONE.value, TodoStatus.NOT_DONE.value}


def editable(day: date, today: date | None = None) -> bool:
    """Today and yesterday (finishing yesterday's list in the morning) and up to a month ahead. Older days are a record."""
    today = today or institute_today()
    return today - timedelta(days=1) <= day <= today + timedelta(days=PLAN_AHEAD_DAYS)


def _out(i: TodoItem, today: date) -> dict:
    return {"id": str(i.id), "work_date": i.work_date.isoformat(), "title": i.title, "note": i.note, "status": i.status,
            "status_label": STATUS_LABEL.get(i.status, i.status), "done_at": i.done_at.isoformat() if i.done_at else None,
            "carried": i.carried_from_id is not None, "editable": editable(i.work_date, today) and i.status != TodoStatus.CARRIED_OVER.value}


def parse_month(month: str | None) -> tuple[date, date]:
    today = institute_today()
    try:
        y, m = (int(x) for x in (month or f"{today:%Y-%m}").split("-"))
        first = date(y, m, 1)
    except (ValueError, TypeError) as exc:
        raise ValidationAppError("Month must look like 2026-11.") from exc
    return first, date(y, m, monthrange(y, m)[1])


class TodoService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ my list
    async def day(self, user_id: uuid.UUID, day: date) -> dict:
        today = institute_today()
        items = (await self.session.execute(select(TodoItem).where(TodoItem.user_id == user_id, TodoItem.work_date == day)
                                            .order_by(TodoItem.position, TodoItem.created_at))).scalars().all()
        # The most recent earlier day that still has unfinished items (offered as "bring them over").
        prev = (await self.session.execute(select(TodoItem.work_date, func.count()).where(
            TodoItem.user_id == user_id, TodoItem.work_date < day, TodoItem.work_date >= day - timedelta(days=14),
            TodoItem.status.in_([TodoStatus.TODO.value, TodoStatus.IN_PROGRESS.value]))
            .group_by(TodoItem.work_date).order_by(TodoItem.work_date.desc()).limit(1))).first()
        done = sum(1 for i in items if i.status == TodoStatus.DONE.value)
        counted = sum(1 for i in items if i.status != TodoStatus.CARRIED_OVER.value)
        return {"date": day.isoformat(), "today": today.isoformat(), "editable": editable(day, today),
                "items": [_out(i, today) for i in items], "done": done, "total": counted,
                "unfinished_before": {"date": prev[0].isoformat(), "count": prev[1]} if prev and editable(day, today) else None}

    async def _own(self, item_id: uuid.UUID, user_id: uuid.UUID) -> TodoItem:
        item = await self.session.get(TodoItem, item_id)
        if item is None or item.user_id != user_id:
            raise ResourceNotFoundError("That to-do item was not found.")
        return item

    async def add(self, user_id: uuid.UUID, *, work_date: date, title: str, note: str | None) -> dict:
        title = " ".join((title or "").split())
        if len(title) < 2:
            raise ValidationAppError("Write what you plan to do.")
        if not editable(work_date):
            raise BusinessRuleViolationError("You can add to today's list, yesterday's, or plan up to a month ahead. Older days are kept as they were.")
        n = (await self.session.execute(select(func.count()).select_from(TodoItem).where(TodoItem.user_id == user_id, TodoItem.work_date == work_date))).scalar_one()
        if n >= MAX_PER_DAY:
            raise BusinessRuleViolationError(f"At most {MAX_PER_DAY} items a day.")
        item = TodoItem(user_id=user_id, work_date=work_date, title=title[:200], note=(note or "").strip()[:1000] or None, position=n)
        self.session.add(item)
        await self.session.flush()
        return _out(item, institute_today())

    async def update(self, user_id: uuid.UUID, item_id: uuid.UUID, changes: dict) -> dict:
        item = await self._own(item_id, user_id)
        if not editable(item.work_date) or item.status == TodoStatus.CARRIED_OVER.value:
            raise BusinessRuleViolationError("This day is closed: lists older than yesterday (and items carried over) stay as they were.")
        if "title" in changes and changes["title"] is not None:
            title = " ".join(changes["title"].split())
            if len(title) < 2:
                raise ValidationAppError("Write what you plan to do.")
            item.title = title[:200]
        if "note" in changes:
            item.note = (changes["note"] or "").strip()[:1000] or None
        if changes.get("status"):
            status = changes["status"]
            if status not in SETTABLE:
                raise ValidationAppError("Choose To do, In progress, Done or Not done.")
            if status == TodoStatus.DONE.value and item.status != status:
                item.done_at = datetime.now(timezone.utc)
            elif status != TodoStatus.DONE.value:
                item.done_at = None
            item.status = status
        await self.session.flush()
        return _out(item, institute_today())

    async def delete(self, user_id: uuid.UUID, item_id: uuid.UUID) -> None:
        item = await self._own(item_id, user_id)
        today = institute_today()
        if item.work_date < today or item.status in (TodoStatus.DONE.value, TodoStatus.CARRIED_OVER.value):
            raise BusinessRuleViolationError("Only today's or future items that are not done can be removed. Mark it Not done instead.")
        await self.session.delete(item)
        await self.session.flush()

    async def carry_over(self, user_id: uuid.UUID, *, to_date: date | None = None) -> dict:
        """Copy the unfinished items of the latest earlier day (within two weeks) onto `to_date` (today)."""
        to_date = to_date or institute_today()
        if not editable(to_date):
            raise BusinessRuleViolationError("Bring items over to today or a day ahead, not to an older day.")
        rows = (await self.session.execute(select(TodoItem).where(
            TodoItem.user_id == user_id, TodoItem.work_date < to_date, TodoItem.work_date >= to_date - timedelta(days=14),
            TodoItem.status.in_([TodoStatus.TODO.value, TodoStatus.IN_PROGRESS.value])).order_by(TodoItem.work_date, TodoItem.position))).scalars().all()
        if not rows:
            return {"moved": 0}
        n = (await self.session.execute(select(func.count()).select_from(TodoItem).where(TodoItem.user_id == user_id, TodoItem.work_date == to_date))).scalar_one()
        if n + len(rows) > MAX_PER_DAY:
            raise BusinessRuleViolationError(f"That would put more than {MAX_PER_DAY} items on one day.")
        for k, old in enumerate(rows):
            self.session.add(TodoItem(user_id=user_id, work_date=to_date, title=old.title, note=old.note, position=n + k,
                                      status=TodoStatus.TODO.value if old.status == TodoStatus.TODO.value else TodoStatus.IN_PROGRESS.value,
                                      carried_from_id=old.id))
            old.status = TodoStatus.CARRIED_OVER.value
        await self.session.flush()
        return {"moved": len(rows)}

    # ------------------------------------------------------------------ administration: monthly report
    async def _people(self) -> dict[uuid.UUID, dict]:
        from app.employees.models import Employee, EmployeeStatus

        rows = (await self.session.execute(select(Employee.user_id, Employee.employee_code, Employee.first_name, Employee.last_name, Employee.department,
                                                  Employee.status).where(Employee.user_id.is_not(None)))).all()
        return {r[0]: {"user_id": str(r[0]), "code": r[1], "name": f"{r[2]} {r[3]}", "department": r[4] or "",
                       "active": r[5] != EmployeeStatus.TERMINATED} for r in rows}

    @staticmethod
    def _effective(item: TodoItem, today: date) -> str:
        """For the report: an item still To do / In progress on a day that has passed was not done that day."""
        if item.work_date < today and item.status in (TodoStatus.TODO.value, TodoStatus.IN_PROGRESS.value):
            return "LEFT_OPEN"
        return item.status

    async def report(self, month: str | None) -> dict:
        first, last = parse_month(month)
        today = institute_today()
        people = await self._people()
        items = (await self.session.execute(select(TodoItem).where(TodoItem.work_date.between(first, last)))).scalars().all()
        stats: dict[uuid.UUID, dict] = {}
        for i in items:
            s = stats.setdefault(i.user_id, {"days": set(), "items": 0, "done": 0, "in_progress": 0, "not_done": 0, "left_open": 0, "carried": 0, "last": None})
            s["days"].add(i.work_date)
            s["last"] = max(s["last"] or i.work_date, i.work_date)
            eff = self._effective(i, today)
            if eff == TodoStatus.CARRIED_OVER.value:
                s["carried"] += 1
                continue                                       # counted on the day it was carried to
            s["items"] += 1
            s["done"] += eff == TodoStatus.DONE.value
            s["in_progress"] += eff == TodoStatus.IN_PROGRESS.value
            s["not_done"] += eff == TodoStatus.NOT_DONE.value
            s["left_open"] += eff == "LEFT_OPEN"
        working_days = sum(1 for d in range((min(last, today) - first).days + 1) if (first + timedelta(days=d)).weekday() != 6) if first <= today else 0
        rows = []
        for uid in set(people) | set(stats):
            p = people.get(uid)
            s = stats.get(uid)
            if p is None:
                from app.auth.models import User

                name = (await self.session.execute(select(User.username).where(User.id == uid))).scalar_one_or_none() or "?"
                p = {"user_id": str(uid), "code": "", "name": name, "department": "", "active": True}
            if not p["active"] and s is None:
                continue
            s = s or {"days": set(), "items": 0, "done": 0, "in_progress": 0, "not_done": 0, "left_open": 0, "carried": 0, "last": None}
            rows.append({**p, "days_with_list": len(s["days"]), "items": s["items"], "done": s["done"], "in_progress": s["in_progress"],
                         "not_done": s["not_done"], "left_open": s["left_open"], "carried": s["carried"],
                         "done_pct": round(100 * s["done"] / s["items"]) if s["items"] else None,
                         "last_day": s["last"].isoformat() if s["last"] else None})
        rows.sort(key=lambda r: (r["items"] == 0, r["name"].lower()))
        totals = {k: sum(r[k] for r in rows) for k in ("items", "done", "in_progress", "not_done", "left_open", "carried")}
        totals["people_with_lists"] = sum(1 for r in rows if r["days_with_list"])
        totals["people"] = len(rows)
        totals["done_pct"] = round(100 * totals["done"] / totals["items"]) if totals["items"] else None
        return {"month": f"{first:%Y-%m}", "label": f"{first:%B %Y}", "working_days_so_far": working_days, "totals": totals, "people": rows}

    async def person_month(self, user_id: uuid.UUID, month: str | None) -> dict:
        first, last = parse_month(month)
        today = institute_today()
        people = await self._people()
        items = (await self.session.execute(select(TodoItem).where(TodoItem.user_id == user_id, TodoItem.work_date.between(first, last))
                                            .order_by(TodoItem.work_date.desc(), TodoItem.position, TodoItem.created_at))).scalars().all()
        days: dict[str, list] = {}
        for i in items:
            row = _out(i, today)
            row["editable"] = False
            row["effective"] = self._effective(i, today)
            days.setdefault(i.work_date.isoformat(), []).append(row)
        return {"month": f"{first:%Y-%m}", "label": f"{first:%B %Y}", "person": people.get(user_id) or {"user_id": str(user_id), "name": "?", "code": "", "department": ""},
                "days": [{"date": d, "items": v} for d, v in days.items()]}

    async def csv_month(self, month: str | None, user_id: uuid.UUID | None = None) -> tuple[str, str]:
        from app.employees.router import _csv_safe

        first, last = parse_month(month)
        today = institute_today()
        people = await self._people()
        stmt = select(TodoItem).where(TodoItem.work_date.between(first, last))
        if user_id:
            stmt = stmt.where(TodoItem.user_id == user_id)
        items = (await self.session.execute(stmt.order_by(TodoItem.work_date, TodoItem.user_id, TodoItem.position))).scalars().all()
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Date", "Day", "Employee code", "Name", "Department", "Task", "Note", "Status", "Done at", "Carried over from an earlier day"])
        label = {**STATUS_LABEL, "LEFT_OPEN": "Left open (not done)"}
        for i in items:
            p = people.get(i.user_id, {"code": "", "name": "?", "department": ""})
            done = i.done_at.astimezone(institute_now().tzinfo).strftime("%Y-%m-%d %H:%M") if i.done_at else ""
            w.writerow([i.work_date.isoformat(), f"{i.work_date:%A}", _csv_safe(p["code"]), _csv_safe(p["name"]), _csv_safe(p["department"]),
                        _csv_safe(i.title), _csv_safe(i.note or ""), label.get(self._effective(i, today), i.status), done, "Yes" if i.carried_from_id else ""])
        who = ("-" + (people.get(user_id, {}).get("code") or "person")) if user_id else ""
        return "﻿" + out.getvalue(), f"todo-{first:%Y-%m}{who}.csv"