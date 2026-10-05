"""Redis client — used for rate limiting, refresh-token revocation, caching,
and as the background job queue backend. Never the source of truth for
permanent LMS data (Section 26)."""
from __future__ import annotations

from functools import lru_cache

import redis.asyncio as redis

from app.core.config import settings


@lru_cache
def get_redis() -> redis.Redis:
    return redis.from_url(settings.redis_url, decode_responses=True)


async def check_redis_connection() -> bool:
    try:
        client = get_redis()
        await client.ping()
        return True
    except Exception:
        return False
