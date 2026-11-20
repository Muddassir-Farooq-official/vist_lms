"""Tell the administrators when backups are in trouble: the last backup failed, or none has finished for 26 hours
(it runs every night at 2:30 AM), or the monthly restore test failed. Run by the sender (scripts/sms_sender.py) every
30 seconds; each problem is announced at most once a day, and again straight away if it changes."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)
REPEAT = timedelta(hours=24)
MARK = "backup-alerted"            # service_heartbeats row: when the last alert went out (detail = which problem)


async def current_problem(session: AsyncSession) -> str | None:
    from app.backups.models import BackupRun
    from app.core.background import backup_status

    problem = (await backup_status(session))["problem"]
    if problem:
        return problem
    verify = (await session.execute(select(BackupRun).where(BackupRun.kind == "VERIFY")
                                    .order_by(BackupRun.finished_at.desc()).limit(1))).scalar_one_or_none()
    if verify is not None and not verify.ok:
        return f"The monthly restore test FAILED: {verify.detail or 'see backups/backup.log on the server'}."
    return None


def _key(problem: str) -> str:
    # "...was 27 hours ago" and "...28 hours ago" are the same problem: digits do not count
    return hashlib.sha256("".join(c for c in problem if not c.isdigit()).encode()).hexdigest()[:16]


async def backup_alert_round(session: AsyncSession) -> int:
    """Returns how many administrators were told. Never raises."""
    try:
        async with session.begin_nested():
            return await _round(session)
    except Exception as exc:  # noqa: BLE001 - an alert problem must not stop sending
        logger.warning("backup_alert_failed", error=str(exc)[:300])
        return 0


async def _round(session: AsyncSession) -> int:
    from app.backups.router import _admins
    from app.core.background import beat
    from app.notifications.email_models import ServiceHeartbeat
    from app.notifications.models import NotificationCategory
    from app.notifications.service import create_notification

    problem = await current_problem(session)
    mark = await session.get(ServiceHeartbeat, MARK)
    if problem is None:
        if mark is not None and mark.detail:
            await beat(session, MARK, "")              # solved: the next problem is announced straight away
        return 0
    key = _key(problem)
    now = datetime.now(timezone.utc)
    if mark is not None and mark.detail == key and now - mark.last_seen_at < REPEAT:
        return 0
    admins = await _admins(session)
    for a in admins:
        await create_notification(session, user_id=a.id, category=NotificationCategory.GENERAL, title="Backup problem",
                                  message=problem + " Open Backups to see the details.", link="/dashboard/admin/backups")
    await beat(session, MARK, key)
    return len(admins)
