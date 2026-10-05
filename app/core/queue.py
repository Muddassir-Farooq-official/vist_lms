"""RQ needs a SYNCHRONOUS redis connection — it's built on the plain
`redis` library, not `redis.asyncio` (which app/core/redis.py provides
for the rest of the app). This is a second, independent connection
helper for exactly that reason, not a duplicate of the existing one.

The queue itself is enqueued from the (async) FastAPI request handler —
enqueueing is a fast, synchronous Redis call, so doing it inline from an
async endpoint is fine and is RQ's normal usage pattern; the actual
job execution happens in a separate `rq worker` process (see
app/imports/tasks.py and the `worker` service in docker-compose.yml),
never inside the web process itself.
"""
from __future__ import annotations

import redis
from rq import Queue

from app.core.config import settings

IMPORTS_QUEUE_NAME = "imports"


def get_sync_redis() -> redis.Redis:
    return redis.from_url(settings.redis_url)


def get_imports_queue() -> Queue:
    return Queue(IMPORTS_QUEUE_NAME, connection=get_sync_redis())