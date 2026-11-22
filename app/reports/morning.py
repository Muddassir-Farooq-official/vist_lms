"""The morning summary for administrators: money, attendance, work waiting and problems, in one short message.

Sent from 8 AM, Monday to Saturday (not Sunday), once a day, to every active administrator as a notification; the
notification is also emailed to anyone with email notices on (app/notifications/email_service.py). Monday's covers
Saturday and Sunday. The same summary is on the Morning summary page any time (worked out fresh).

Every figure comes from the calculations the other screens use; nothing here changes anything.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)
MARK = "morning-summary"
HOUR = 8
PAGE = "/dashboard/admin/morning-summary"


def covered(today: date) -> tuple[date, date]:
    """The days the summary looks back on: yesterday; on Monday, Saturday and Sunday."""
    last = today - timedelta(days=1)
    first = today - timedelta(days=2) if today.weekday() == 0 else last
    return first, last


def _money(v) -> str:
    return f"Rs. {float(v or 0):,.0f}"


def _label(first: date, last: date) -> str:
    return f"{first:%a %d %b}" if first == last else f"{first:%a %d} – {last:%a %d %b}"


async def _utc_bounds(first: date, last: date) -> tuple[datetime, datetime]:
    from zoneinfo import ZoneInfo

    from app.core.config import settings

    tz = ZoneInfo(settings.institute_timezone)
    return (datetime.combine(first, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc),
            datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc))


async def _admin_permissions(session: AsyncSession) -> set[str]:
    from app.auth.models import Permission, Role, RolePermission
    from app.auth.permissions import Roles

    return set((await session.execute(select(Permission.code).join(RolePermission, RolePermission.permission_id == Permission.id)
                                      .join(Role, Role.id == RolePermission.role_id).where(Role.code == Roles.ADMIN))).scalars())


async def _safe(name: str, coro, problems: list[str]):
    """One part failing must not lose the rest of the summary."""
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        logger.warning("morning_summary_part_failed", part=name, error=str(exc)[:300])
        problems.append(f"The {name} part of this summary could not be worked out.")
        return None


async def build(session: AsyncSession, today: date | None = None) -> dict:
    from app.core.clock import institute_today

    today = today or institute_today()
    first, last = covered(today)
    errors: list[str] = []
    out = {"today": today, "from_date": first, "to_date": last, "label": _label(first, last)}
    out["money"] = await _safe("money", _money_part(session, first, last, today), errors)
    out["attendance"] = await _safe("attendance", _attendance_part(session, first, last), errors)
    out["waiting"] = await _safe("work waiting", _waiting_part(session, last), errors)
    out["problems"] = (await _safe("problems", _problems_part(session, first, last, today), errors) or []) + errors
    out["text"] = render(out)
    return out


async def _money_part(session, first, last, today) -> dict:
    from app.fees.models import FeePromise
    from app.reports.finance_service import FinanceReportService

    fin = FinanceReportService(session)
    col = await fin.collection(first, last)
    d = await fin.defaulters()
    overdue = [p for p in d["students"] if p["overdue"] > 0.005]
    promises = int((await session.execute(select(func.count()).select_from(FeePromise).where(
        FeePromise.promised_date == today, FeePromise.cancelled_at.is_(None)))).scalar_one())
    return {"received": col["received"], "net": col["total"], "payments": sum(g["count"] for g in col["rows"]),
            "overdue": round(sum(p["overdue"] for p in overdue), 2), "defaulters": len(overdue), "promises_today": promises}


async def _attendance_part(session, first, last) -> dict:
    from app.academics.models import ClassSession, ClassSessionStatus
    from app.attendance.models import AttendanceRecord
    from app.reports.monthly import MonthlyReport
    from app.timekeeping.service import TimekeepingService

    classes = await MonthlyReport(session)._classes(first, last)
    taken = select(AttendanceRecord.class_session_id).where(AttendanceRecord.class_session_id == ClassSession.id).exists()
    not_marked = int((await session.execute(select(func.count()).select_from(ClassSession).where(
        ClassSession.session_date >= first, ClassSession.session_date <= last, ClassSession.status == ClassSessionStatus.SCHEDULED, ~taken))).scalar_one())
    staff = {"working": 0, "absent": 0, "late": 0, "leave": 0}
    for day in (first + timedelta(days=i) for i in range((last - first).days + 1)):
        for p in await TimekeepingService(session).board(day=day, q=None):
            if not p["working_today"]:
                continue
            staff["working"] += 1
            if p["state"] == "EXCUSED":
                staff["leave"] += 1
            elif p["state"] == "NOT_IN":
                staff["absent"] += 1
            elif (p["day"] or {}).get("late_minutes"):
                staff["late"] += 1
    return {"classes": classes["held"], "average": classes["average"], "low": classes["low"], "not_marked": not_marked, "staff": staff}


async def _waiting_part(session, last) -> dict:
    from app.applications.models import Application, ApplicationStatus
    from app.audit.money_review import listing
    from app.core.nav_badges import waiting_counts

    counts = await waiting_counts(session, await _admin_permissions(session))
    counts["applications"] = int((await session.execute(select(func.count()).select_from(Application).where(
        Application.status == ApplicationStatus.SUBMITTED))).scalar_one())
    try:
        counts["money_review"] = int((await listing(session, day=last, unchecked_only=True))["unchecked"])
    except Exception:  # noqa: BLE001 - optional part
        counts["money_review"] = 0
    return counts


async def _problems_part(session, first, last, today) -> list[str]:
    from app.core.background import backup_status
    from app.serverhealth.service import latest, problems
    from app.sms.models import SmsMessage, SmsStatus
    from app.students.regdeadline import overview

    found = list(problems(await latest(session)))
    b = (await backup_status(session)).get("problem")
    if b:
        found.append(str(b))
    start, end = await _utc_bounds(first, last)
    failed = int((await session.execute(select(func.count()).select_from(SmsMessage).where(
        SmsMessage.status == SmsStatus.FAILED, SmsMessage.created_at >= start, SmsMessage.created_at < end))).scalar_one())
    if failed:
        found.append(f"{failed} SMS could not be sent.")
    for t in (await overview(session))["terms"]:
        if t["reg_deadline"] and t["missing"] and t["days_left"] is not None and t["days_left"] <= 7:
            when = "the last day has passed" if t["days_left"] < 0 else ("today is the last day" if t["days_left"] == 0 else f"{t['days_left']} day(s) left")
            found.append(f"{t['label']}: {t['missing']} university Reg No(s) missing ({when}).")
    return found


WAITING_LABELS = (("approvals", "approval"), ("admissions", "admission form"), ("profile_changes", "profile change"),
                  ("certificates", "certificate request"), ("result_sheets", "result sheet to approve"), ("applications", "application"),
                  ("money_review", "money change to review"))


def render(r: dict) -> str:
    lines = []
    m = r.get("money")
    if m:
        s = f"Money: {_money(m['received'])} received {r['label']} ({m['payments']} payment(s)). Overdue now {_money(m['overdue'])} from {m['defaulters']} student(s)."
        if m["promises_today"]:
            s += f" {m['promises_today']} promise(s) to pay fall due today."
        lines.append(s)
    a = r.get("attendance")
    if a:
        s = f"Attendance: {a['classes']} class(es)" + (f", average {a['average']:g}%" if a["average"] is not None else "") + "."
        if a["not_marked"]:
            s += f" {a['not_marked']} class(es) not marked."
        st = a["staff"]
        if st["working"]:
            s += f" Staff: {st['absent']} absent, {st['late']} late, {st['leave']} on leave."
        lines.append(s)
    w = r.get("waiting")
    if w is not None:
        parts = [f"{w[k]} {lbl}{'' if w[k] == 1 else 's'}" for k, lbl in WAITING_LABELS if w.get(k)]
        lines.append("Waiting: " + (", ".join(parts) if parts else "nothing") + ".")
    p = r.get("problems") or []
    lines.append("Problems: " + (" ".join(p) if p else "none."))
    return "\n".join(lines)


async def morning_round(session: AsyncSession, now: datetime | None = None) -> int:
    """Run by the sender every 30 seconds; works once a day from 8 AM, Monday to Saturday. Never raises."""
    try:
        async with session.begin_nested():
            return await _round(session, now)
    except Exception as exc:  # noqa: BLE001 - a summary problem must not stop sending
        logger.warning("morning_summary_failed", error=str(exc)[:300])
        return 0


async def _round(session: AsyncSession, now: datetime | None) -> int:
    from app.backups.router import _admins
    from app.core.background import beat
    from app.core.clock import institute_now
    from app.core.config import settings
    from app.notifications.email_models import ServiceHeartbeat
    from app.notifications.models import NotificationCategory
    from app.notifications.service import create_notification

    now = now or institute_now()
    today = now.date()
    if now.hour < HOUR or today.weekday() == 6 or not int(getattr(settings, "morning_summary", 1)):
        return 0
    mark = await session.get(ServiceHeartbeat, MARK)
    if mark is not None and (mark.detail or "").startswith(today.isoformat()):
        return 0
    r = await build(session, today)
    text = r["text"]
    if len(text) > 990:
        text = text[:960].rstrip() + " … (open for the rest)"
    n = 0
    for a in await _admins(session):
        await create_notification(session, user_id=a.id, category=NotificationCategory.GENERAL,
                                  title=f"Morning summary — {today:%a %d %b}", message=text, link=PAGE)
        n += 1
    await beat(session, MARK, f"{today.isoformat()}: sent to {n}")
    return n


# ---------------------------------------------------------------------- the page
from fastapi import APIRouter, Depends  # noqa: E402

from app.auth.permissions import Roles  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.core.dependencies import CurrentUser, require_any_role  # noqa: E402

router = APIRouter(prefix="/morning-summary", tags=["reports"])


@router.get("", summary="Today's morning summary, worked out now (administrators)")
async def morning_summary(current_user: CurrentUser = Depends(require_any_role(Roles.ADMIN)), db=Depends(get_db)) -> dict:
    from app.notifications.email_models import ServiceHeartbeat

    r = await build(db)
    mark = await db.get(ServiceHeartbeat, MARK)
    r["last_sent"] = mark.detail if mark else None
    return r
