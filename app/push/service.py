"""Push notifications to people's phones and browsers.

Every notification the portal already creates (application forwarded, fee reminder, security alert, to-do reminder
...) is also pushed to the person's devices that turned push on. The background sender (scripts/sms_sender.py, or the
web app's own loop when that service is stopped) picks up new notifications every 30-60 seconds, so no other part of
the code has to change. Nothing is pushed when the VAPID keys are not set.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ValidationAppError
from app.core.logging import get_logger
from app.push.models import PushSubscription
from app.push.webpush import encrypt, vapid_headers

logger = get_logger(__name__)

LOOK_BACK = timedelta(minutes=15)      # only notifications this recent are pushed (older ones are not news any more)
MAX_DEVICES = 10
GIVE_UP_AFTER = 5                      # failed sends in a row before a device is forgotten


async def deliver(client: httpx.AsyncClient, endpoint: str, body: bytes, headers: dict) -> httpx.Response:
    """One encrypted message to one device's push service (Google, Mozilla, Apple, Microsoft ...)."""
    return await client.post(endpoint, content=body, headers=headers)


def enabled() -> bool:
    return bool(settings.vapid_public_key and settings.vapid_private_key)


def device_name(user_agent: str | None) -> str:
    ua = (user_agent or "").lower()
    browser = "Edge" if "edg/" in ua else "Samsung Internet" if "samsungbrowser" in ua else "Chrome" if "chrome" in ua else \
        "Firefox" if "firefox" in ua else "Safari" if "safari" in ua else "Browser"
    system = "iPhone" if "iphone" in ua else "iPad" if "ipad" in ua else "Android" if "android" in ua else "Windows" if "windows" in ua else \
        "Mac" if "mac os" in ua else "Linux" if "linux" in ua else ""
    return f"{browser} on {system}" if system else browser


class PushService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ devices
    async def subscribe(self, user_id: uuid.UUID, *, endpoint: str, p256dh: str, auth: str, user_agent: str | None) -> dict:
        if not enabled():
            raise ValidationAppError("Phone notifications are not set up on this server yet.")
        if not endpoint.startswith("https://") or len(endpoint) > 2000 or not p256dh or not auth:
            raise ValidationAppError("This browser sent an unusable push subscription.")
        row = (await self.session.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint))).scalars().first()
        if row is None:
            count = (await self.session.execute(select(func.count()).select_from(PushSubscription).where(PushSubscription.user_id == user_id))).scalar_one()
            if count >= MAX_DEVICES:                          # forget the oldest device rather than refuse
                oldest = (await self.session.execute(select(PushSubscription).where(PushSubscription.user_id == user_id)
                                                     .order_by(PushSubscription.created_at).limit(1))).scalars().first()
                await self.session.delete(oldest)
            row = PushSubscription(endpoint=endpoint, user_id=user_id, p256dh=p256dh[:200], auth=auth[:100])
            self.session.add(row)
        row.user_id, row.p256dh, row.auth, row.failures = user_id, p256dh[:200], auth[:100], 0     # a shared device follows whoever signed in last
        row.device = device_name(user_agent)[:120]
        await self.session.flush()
        return {"devices": await self.count(user_id)}

    async def unsubscribe(self, user_id: uuid.UUID, endpoint: str) -> dict:
        await self.session.execute(delete(PushSubscription).where(PushSubscription.user_id == user_id, PushSubscription.endpoint == endpoint))
        return {"devices": await self.count(user_id)}

    async def count(self, user_id: uuid.UUID) -> int:
        return (await self.session.execute(select(func.count()).select_from(PushSubscription).where(PushSubscription.user_id == user_id))).scalar_one()

    # ------------------------------------------------------------------ sending
    async def _send_one(self, client: httpx.AsyncClient, sub: PushSubscription, payload: dict) -> bool:
        """True when the push service accepted it. A device that is gone (404 / 410) is forgotten."""
        body = encrypt(json.dumps(payload, ensure_ascii=False).encode(), sub.p256dh, sub.auth)
        headers = {**vapid_headers(sub.endpoint, public_key=settings.vapid_public_key, private_key=settings.vapid_private_key,
                                   subject=settings.vapid_subject or f"mailto:{settings.institute_email or 'admin@example.com'}"),
                   "TTL": "86400", "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "Urgency": "normal"}
        try:
            r = await deliver(client, sub.endpoint, body, headers)
        except httpx.HTTPError as exc:
            logger.warning("push_failed", error=str(exc)[:200])
            sub.failures += 1
            return False
        if r.status_code in (404, 410):
            await self.session.delete(sub)
            return False
        if r.status_code >= 400:
            logger.warning("push_refused", status=r.status_code, body=r.text[:200])
            sub.failures += 1
            if sub.failures >= GIVE_UP_AFTER:
                await self.session.delete(sub)
            return False
        sub.failures, sub.last_sent_at = 0, datetime.now(timezone.utc)
        return True

    async def send_to(self, user_ids: list, payload: dict) -> int:
        if not enabled() or not user_ids:
            return 0
        subs = (await self.session.execute(select(PushSubscription).where(PushSubscription.user_id.in_(user_ids)))).scalars().all()
        sent = 0
        async with httpx.AsyncClient(timeout=10) as client:
            for sub in subs:
                sent += await self._send_one(client, sub, payload)
        await self.session.flush()
        return sent

    async def push_new_notifications(self, *, limit: int = 300) -> dict:
        """Push the notifications created in the last few minutes that have not been pushed yet."""
        from app.notifications.models import Notification

        if not enabled():
            return {"pushed": 0}
        since = datetime.now(timezone.utc) - LOOK_BACK
        rows = (await self.session.execute(select(Notification).where(Notification.pushed_at.is_(None), Notification.created_at >= since)
                                           .order_by(Notification.created_at).limit(limit).with_for_update(skip_locked=True))).scalars().all()
        if not rows:
            return {"pushed": 0}
        with_devices = set((await self.session.execute(select(PushSubscription.user_id).where(
            PushSubscription.user_id.in_({n.user_id for n in rows})))).scalars().all())
        now = datetime.now(timezone.utc)
        pushed = 0
        async with httpx.AsyncClient(timeout=10) as client:
            for n in rows:
                if n.user_id in with_devices:
                    payload = {"title": n.title, "body": n.message[:300], "url": n.link or "/", "tag": str(n.id)}
                    subs = (await self.session.execute(select(PushSubscription).where(PushSubscription.user_id == n.user_id))).scalars().all()
                    for sub in subs:
                        pushed += await self._send_one(client, sub, payload)
        await self.session.execute(update(Notification).where(Notification.id.in_([n.id for n in rows])).values(pushed_at=now))
        await self.session.flush()
        return {"pushed": pushed}