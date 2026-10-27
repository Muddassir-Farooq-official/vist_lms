"""Staff performance scorecard: one score out of 100 per employee per month, from records the system already keeps.

    Attendance   40   days present / working days
    Punctuality  25   on-time days / days present (late arrivals and early leavings count against it)
    Tasks        25   tasks due that month: done on time = full, done late = half, still overdue = none
    Conduct      10   complaints against them that month (not dismissed or withdrawn): -25 each

A part with nothing to measure (no working days, no tasks) is left out and the rest are weighted up, so nobody is
penalised for having no tasks. Read-only: it never changes pay; a bonus is always the administration's decision.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.complaints.models import Complaint, ComplaintPartyType, ComplaintStatus
from app.core.clock import institute_today
from app.employees.models import Employee, EmployeeBonus, EmployeeStatus, JobRole
from app.payroll import rules
from app.tasks.models import Task, TaskStatus

WEIGHTS = {"attendance": 40, "punctuality": 25, "tasks": 25, "conduct": 10}
ACTIVE = (EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE)


def grade(score: int | None) -> str:
    if score is None:
        return "Not enough data"
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 60:
        return "Fair"
    return "Needs attention"


def combine(parts: dict[str, float | None]) -> int | None:
    """Weighted average of the parts that have something to measure (each 0..1). Attendance is required to score at all."""
    if parts.get("attendance") is None:
        return None
    have = {k: v for k, v in parts.items() if v is not None}
    total = sum(WEIGHTS[k] for k in have)
    return round(100 * sum(WEIGHTS[k] * max(0.0, min(1.0, v)) for k, v in have.items()) / total) if total else None


class PerformanceService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def scorecard(self, month: date) -> dict:
        from app.timekeeping.service import TimekeepingService

        first, last = rules.month_start(month), rules.month_end(month)
        through = min(last, institute_today())
        people = (await self.session.execute(select(Employee, JobRole.title).join(JobRole, JobRole.id == Employee.job_role_id)
                                             .where(Employee.status.in_(ACTIVE), Employee.hire_date <= last)
                                             .order_by(Employee.first_name, Employee.last_name))).all()
        emps = [e for e, _ in people]
        att = await TimekeepingService(self.session).summary(first, emps) if emps else {}

        # Tasks due in the month, per assignee (the person's login).
        start_dt = datetime.combine(first, time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(last + timedelta(days=1), time.min, tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        user_ids = [e.user_id for e in emps if e.user_id]
        tasks: dict[uuid.UUID, dict] = {}
        if user_ids:
            for t in (await self.session.execute(select(Task).where(Task.assignee_user_id.in_(user_ids), Task.deadline >= start_dt, Task.deadline < end_dt,
                                                                   Task.status.notin_([TaskStatus.CANCELLED, TaskStatus.REJECTED])))).scalars():
                c = tasks.setdefault(t.assignee_user_id, {"on_time": 0, "late": 0, "overdue": 0, "open": 0})
                done = t.completed_at
                if done is not None:
                    c["on_time" if done <= t.deadline else "late"] += 1
                elif t.deadline < now:
                    c["overdue"] += 1
                else:
                    c["open"] += 1

        complaints = dict((await self.session.execute(
            select(Complaint.target_id, func.count()).where(Complaint.target_type == ComplaintPartyType.EMPLOYEE, Complaint.created_at >= start_dt,
                                                            Complaint.created_at < end_dt, Complaint.status.notin_([ComplaintStatus.DISMISSED, ComplaintStatus.WITHDRAWN]))
            .group_by(Complaint.target_id))).all())
        # A month's bonus is usually given early the next month, so both count as "already given for this month".
        bonus_until = rules.month_end(last + timedelta(days=1))
        bonuses = dict((await self.session.execute(
            select(EmployeeBonus.employee_id, func.sum(EmployeeBonus.amount)).where(EmployeeBonus.awarded_at >= first, EmployeeBonus.awarded_at <= bonus_until)
            .group_by(EmployeeBonus.employee_id))).all())

        rows = []
        for e, title in people:
            a = att.get(e.id) or {}
            if not a.get("recorded"):                 # nothing recorded for this person this month (as payroll): not scored, never "absent all month"
                a = {}
            scheduled, present = a.get("scheduled", 0), a.get("present", 0)
            on_duty = present - a.get("excused", 0)
            slips = a.get("late", 0) + a.get("early", 0)
            t = tasks.get(e.user_id) or {"on_time": 0, "late": 0, "overdue": 0, "open": 0}
            judged = t["on_time"] + t["late"] + t["overdue"]
            n_compl = int(complaints.get(e.id, 0))
            parts = {
                "attendance": present / scheduled if scheduled else None,
                "punctuality": (1 - slips / on_duty) if on_duty > 0 else None,
                "tasks": (t["on_time"] + 0.5 * t["late"]) / judged if judged else None,
                "conduct": max(0.0, 1 - 0.25 * n_compl),
            }
            score = combine(parts)
            rows.append({
                "employee_id": e.id, "name": f"{e.first_name} {e.last_name}", "employee_code": e.employee_code, "job_title": title,
                "department": e.department, "has_photo": e.has_photo, "score": score, "grade": grade(score),
                "parts": {k: (round(100 * v) if v is not None else None) for k, v in parts.items()},
                "attendance": {"scheduled": scheduled, "present": present, "absent": a.get("absent", 0), "late": a.get("late", 0), "early": a.get("early", 0)},
                "tasks": t, "complaints": n_compl, "bonus_this_month": float(bonuses.get(e.id) or 0),
            })
        ranked = sorted([r for r in rows if r["score"] is not None], key=lambda r: (-r["score"], -(r["parts"]["attendance"] or 0), r["name"]))
        for i, r in enumerate(ranked, start=1):
            r["rank"] = i
        unranked = [dict(r, rank=None) for r in rows if r["score"] is None]
        return {"month": first, "month_label": first.strftime("%B %Y"), "through": through, "complete": through >= last,
                "weights": WEIGHTS, "people": ranked + unranked,
                "average": round(sum(r["score"] for r in ranked) / len(ranked)) if ranked else None}