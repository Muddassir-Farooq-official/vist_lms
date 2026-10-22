"""Tell someone when the server hits an unexpected error (optional webhook: Slack, Discord, ntfy...).

One message per distinct error per quarter-hour, not one per visitor, so a failing page cannot flood the channel.
Sending never raises: an alert that fails must not make the original error worse.
"""
from __future__ import annotations

import asyncio
import time

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_WINDOW_SECONDS = 15 * 60
_last_sent: dict[tuple[str, str, str], float] = {}


async def _post(url: str, text: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            # "text" works for Slack-style hooks, "content" for Discord-style ones.
            await client.post(url, json={"text": text, "content": text})
    except Exception as exc:  # noqa: BLE001
        logger.warning("error_alert_failed", error=str(exc))


def alert_unhandled(exc: BaseException, *, path: str, request_id: str | None = None) -> None:
    url = (settings.error_alert_webhook or "").strip()
    if not url:
        return
    key = (type(exc).__name__, str(exc)[:200], path)
    now = time.monotonic()
    last = _last_sent.get(key)
    if last is not None and now - last < _WINDOW_SECONDS:
        return
    if len(_last_sent) > 500:
        _last_sent.clear()
    _last_sent[key] = now
    text = f"[{settings.app_name}] {type(exc).__name__} on {path}: {str(exc)[:300]}" + (f" (request {request_id})" if request_id else "")
    try:
        asyncio.get_running_loop().create_task(_post(url, text))
    except RuntimeError:
        pass
