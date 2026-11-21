"""Reading the server's health (app/serverhealth/models.py) and deciding what is wrong.

Problems (each one alerts every administrator once a day while it lasts, and again straight away when it changes):
  * disk more than 85% full;
  * an institute's HTTPS certificate expires within 14 days (or cannot be read);
  * an institute's site or its database does not answer (as checked by the server each minute);
  * memory above 90%, or the server overloaded, for 10 minutes or more;
  * the server has stopped reporting for 10 minutes (its helper, `sites.sh jobs`, is not running).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)
DISK_PCT = 85.0
CERT_DAYS = 14
LASTS = timedelta(minutes=10)
STALE = timedelta(minutes=10)
REPEAT = timedelta(hours=24)
MARK = "health-alerted"


def _dt(v):
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def problems(row, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    if row is None:
        return []                                   # never reported: the page says how to set it up; no alert
    out = []
    if now - row.taken_at > STALE:
        out.append(f"The server has not reported its health since {row.taken_at:%d %b %H:%M} UTC: its helper (sites.sh jobs) may have stopped.")
        return out
    d = row.data or {}
    srv = d.get("server") or {}
    if (srv.get("disk_used_pct") or 0) >= DISK_PCT:
        out.append(f"The server's disk is {srv['disk_used_pct']:g}% full.")
    since = _dt(srv.get("memory_high_since"))
    if since and now - since >= LASTS:
        out.append(f"The server's memory has been above 90% since {since:%H:%M} UTC.")
    since = _dt(srv.get("load_high_since"))
    if since and now - since >= LASTS:
        out.append(f"The server has been overloaded since {since:%H:%M} UTC.")
    for s in d.get("sites") or []:
        if not s.get("enabled", True):
            continue
        name = s.get("name")
        if not s.get("up"):
            out.append(f"{name}: the site is not answering properly ({s.get('error') or 'HTTP ' + str(s.get('http_status'))}).")
        elif s.get("database") is False:
            out.append(f"{name}: the site cannot reach its database.")
        end = _dt(s.get("cert_expires"))
        if s.get("domain") and end is not None and end - now < timedelta(days=CERT_DAYS):
            days = max(0, (end - now).days)
            out.append(f"{name}: the HTTPS certificate for {s['domain']} expires in {days} day(s) ({end:%d %b %Y}).")
    return out


async def latest(session: AsyncSession):
    from app.serverhealth.models import ServerHealth

    return await session.get(ServerHealth, "latest")


async def _key(found: list[str]) -> str:
    return hashlib.sha256("|".join(sorted("".join(c for c in p if not c.isdigit()) for p in found)).encode()).hexdigest()[:16]


async def health_alert_round(session: AsyncSession) -> int:
    """Run by the sender every 30 seconds. Never raises."""
    try:
        async with session.begin_nested():
            return await _round(session)
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_alert_failed", error=str(exc)[:300])
        return 0


async def _round(session: AsyncSession) -> int:
    from app.backups.router import _admins
    from app.core.background import beat
    from app.notifications.email_models import ServiceHeartbeat
    from app.notifications.models import NotificationCategory
    from app.notifications.service import create_notification

    found = problems(await latest(session))
    mark = await session.get(ServiceHeartbeat, MARK)
    if not found:
        if mark is not None and mark.detail:
            await beat(session, MARK, "")
        return 0
    key = await _key(found)
    now = datetime.now(timezone.utc)
    if mark is not None and mark.detail == key and now - mark.last_seen_at < REPEAT:
        return 0
    admins = await _admins(session)
    text = " ".join(found[:6]) + (f" (+{len(found) - 6} more)" if len(found) > 6 else "")
    for a in admins:
        await create_notification(session, user_id=a.id, category=NotificationCategory.GENERAL, title="Server problem",
                                  message=text + " Open Server health for details.", link="/dashboard/admin/server-health")
    await beat(session, MARK, key)
    return len(admins)
