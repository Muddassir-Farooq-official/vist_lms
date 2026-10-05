"""System-wide read caching, stale-while-revalidate.

The pattern: a cached read serves whatever's in Redis IMMEDIATELY (fast —
no DB round trip on the request path), then silently kicks off a
background refresh so the *next* read gets current data. A cache miss
falls through to a normal synchronous fetch (so the very first request
after a cold cache, or after Redis is flushed, is never broken — just not
sped up yet).

This deliberately does NOT try to keep the cache perfectly fresh on every
write across the whole system — for a handful of specific
frequently-changed, admin-triggered resources (programs, job roles), the
mutating endpoints explicitly call `invalidate()` so the change is visible
immediately rather than waiting out the TTL. Everything else relies on the
TTL + background-refresh cycle, which is the intended trade-off: bounded
staleness in exchange for speed, matching Section 26 — Redis is a
speed layer, Postgres remains the only source of truth.
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from app.core.redis import get_redis

CACHE_KEY_PREFIX = "vist_lms:cache:"


def _full_key(key: str) -> str:
    return f"{CACHE_KEY_PREFIX}{key}"


async def get_cached(key: str) -> Any | None:
    try:
        raw = await get_redis().get(_full_key(key))
    except Exception:
        # Redis being unavailable must never break a request — treat it
        # exactly like a cache miss and fall through to the real fetch.
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


async def set_cached(key: str, value: Any, ttl_seconds: int) -> None:
    try:
        await get_redis().set(_full_key(key), json.dumps(value), ex=ttl_seconds)
    except Exception:
        pass  # caching is an optimization, never a hard requirement


async def invalidate(key: str) -> None:
    try:
        await get_redis().delete(_full_key(key))
    except Exception:
        pass


async def invalidate_prefix(prefix: str) -> None:
    """Deletes every cached key starting with `prefix` — used when a
    write could affect several cache-key variants at once (e.g. a
    programs list cached separately for active_only=true/false)."""
    try:
        client = get_redis()
        async for k in client.scan_iter(match=f"{_full_key(prefix)}*"):
            await client.delete(k)
    except Exception:
        pass


async def refresh_in_background(key: str, ttl_seconds: int, fetch_fn: Callable[[], Awaitable[Any]]) -> None:
    """Runs the real fetch and repopulates the cache. Meant to be handed
    to FastAPI's BackgroundTasks — note the same gotcha documented in
    app/imports/router.py applies here: BackgroundTasks execute BEFORE a
    yield-dependency's post-yield commit, so `fetch_fn` must use its own
    independent DB session (via AsyncSessionLocal()), never the
    request-scoped one, which may already be gone by the time this runs."""
    try:
        fresh = await fetch_fn()
        await set_cached(key, fresh, ttl_seconds)
    except Exception:
        pass  # a failed background refresh just means the cache stays as
        # it was — never surface this to whichever request happens to
        # trigger it, since that request already got its (possibly
        # stale) response and moved on.