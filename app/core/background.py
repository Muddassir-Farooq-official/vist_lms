"""Background sending, and the status of the background services.

* The `sms` service (scripts/sms_sender.py, docker-compose) sends queued SMS and emails every 30 seconds and records a
  heartbeat ("sender").
* So that nothing waits for ever when that service is stopped, the web app also runs a small loop: every 60 seconds,
  if the sender has not been seen for SENDER_STALE_SECONDS, one web worker (a PostgreSQL advisory lock picks one) sends
  what is due itself and records the "web-app" heartbeat. No cron job is needed either way.
* status() is what the admin dashboard shows: import worker, sender, and anything waiting too long.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)
SENDER_STALE_SECONDS = 120
LOOP_SECONDS = 60
ADVISORY_KEY = 7_740_221          # any fixed number: "only one web worker sends at a time"
WAITING_TOO_LONG = timedelta(hours=1)


async def beat(session: AsyncSession, name: str, detail: str | None = None) -> None:
    from app.notifications.email_models import ServiceHeartbeat

    now = datetime.now(timezone.utc)
    stmt = insert(ServiceHeartbeat).values(name=name, last_seen_at=now, detail=(detail or "")[:500] or None)
    await session.execute(stmt.on_conflict_do_update(index_elements=["name"], set_={"last_seen_at": now, "detail": stmt.excluded.detail}))


async def last_seen(session: AsyncSession, name: str) -> datetime | None:
    from app.notifications.email_models import ServiceHeartbeat

    return (await session.execute(select(ServiceHeartbeat.last_seen_at).where(ServiceHeartbeat.name == name))).scalar_one_or_none()


async def send_due(session: AsyncSession, *, time_budget: float = 20) -> dict:
    """One sending round: queued SMS, then queued emails."""
    from app.notifications.email_service import send_pending as send_emails
    from app.policy.service import apply_from_db
    from app.sms.service import SmsService

    await apply_from_db(session)
    sms = await SmsService(session).send_pending(limit=100, time_budget=time_budget)
    emails = await send_emails(session, limit=100, time_budget=time_budget)
    return {**sms, **emails}


async def _web_round() -> None:
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        seen = await last_seen(session, "sender")
        if seen is not None and (datetime.now(timezone.utc) - seen).total_seconds() < SENDER_STALE_SECONDS:
            return                                                       # the sender service is running: leave it to it
        got = (await session.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_KEY})).scalar_one()
        if not got:
            return                                                       # another web worker is doing this round
        result = await send_due(session)
        await beat(session, "web-app", f"{result.get('processed', 0)} SMS and {result.get('emails', 0)} emails handled")
        await session.commit()


async def web_sender_loop() -> None:
    await asyncio.sleep(20)                                              # let the app finish starting
    while True:
        try:
            await _web_round()
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                         # never take the web app down; try again next round
            logger.warning("web_sender_round_failed", error=str(exc)[:300])
        await asyncio.sleep(LOOP_SECONDS)


def start_web_sender(app) -> None:
    """Called at app start-up (not in tests)."""
    if settings.app_env == "test" or not settings.web_app_sends_when_sender_down:
        return

    @app.on_event("startup")
    async def _start() -> None:
        app.state.web_sender = asyncio.create_task(web_sender_loop())

    @app.on_event("shutdown")
    async def _stop() -> None:
        task = getattr(app.state, "web_sender", None)
        if task:
            task.cancel()


# ------------------------------------------------------------------ what the dashboard shows
def _ago(dt: datetime | None) -> int | None:
    return None if dt is None else int((datetime.now(timezone.utc) - dt).total_seconds())


async def status(session: AsyncSession) -> dict:
    from app.imports.models import ImportJob, ImportJobStatus
    from app.notifications.email_models import EmailOutbox
    from app.notifications.email_service import load_config
    from app.sms.models import SmsMessage, SmsStatus

    now = datetime.now(timezone.utc)
    try:
        from rq import Worker

        from app.core.queue import get_imports_queue

        workers = Worker.count(queue=get_imports_queue())
    except Exception:
        workers = 0
    sender_ago = _ago(await last_seen(session, "sender"))
    web_ago = _ago(await last_seen(session, "web-app"))
    sms_q, sms_oldest = (await session.execute(select(func.count(), func.min(SmsMessage.created_at)).where(SmsMessage.status == SmsStatus.QUEUED))).one()
    em_q, em_oldest = (await session.execute(select(func.count(), func.min(EmailOutbox.created_at)).where(EmailOutbox.status == "PENDING"))).one()
    em_failed = (await session.execute(select(func.count()).select_from(EmailOutbox).where(
        EmailOutbox.status == "FAILED", EmailOutbox.kind != "TEST", EmailOutbox.created_at >= now - timedelta(days=1)))).scalar_one()
    stuck = (await session.execute(select(func.count()).select_from(ImportJob).where(
        ImportJob.status == ImportJobStatus.UPLOADED.value, ImportJob.created_at < now - timedelta(minutes=2)))).scalar_one()
    cfg = await load_config(session)
    sender_ok = sender_ago is not None and sender_ago < SENDER_STALE_SECONDS
    web_ok = web_ago is not None and web_ago < SENDER_STALE_SECONDS * 2

    problems = []
    if not sender_ok:
        problems.append("The background sender (the 'sms' service) is not running"
                        + (": the web app is sending SMS and emails instead, every minute." if web_ok else "."))
    if workers == 0:
        problems.append("The import worker is not running: CSV imports are processed by the web app instead (slower for big files).")
    if sms_q and sms_oldest and now - sms_oldest > WAITING_TOO_LONG:
        problems.append(f"{sms_q} SMS have been waiting for more than an hour.")
    if em_q and em_oldest and now - em_oldest > WAITING_TOO_LONG:
        problems.append(f"{em_q} emails have been waiting for more than an hour.")
    if em_failed:
        problems.append(f"{em_failed} emails failed in the last 24 hours (Settings → Email shows why).")
    if stuck:
        problems.append(f"{stuck} CSV upload(s) never started (upload the same file again to retry).")
    if cfg.enabled and not cfg.ready:
        problems.append("Email is turned on but the mail server or From address is missing (Settings → Email).")
    return {
        "ok": not problems,
        "problems": problems,
        "import_worker": {"running": workers > 0, "workers": workers},
        "sender": {"running": sender_ok, "last_seen_seconds_ago": sender_ago},
        "web_app_sender": {"active": web_ok and not sender_ok, "last_seen_seconds_ago": web_ago},
        "sms": {"waiting": sms_q, "oldest_minutes": int((now - sms_oldest).total_seconds() // 60) if sms_oldest else None,
                "provider_configured": bool(settings.sms_provider_url)},
        "email": {"enabled": cfg.ready, "notify_by_email": cfg.notify_by_email, "waiting": em_q,
                  "oldest_minutes": int((now - em_oldest).total_seconds() // 60) if em_oldest else None, "failed_24h": em_failed},
        "imports_not_started": stuck,
    }