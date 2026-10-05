"""The actual job function RQ calls, in the separate `rq worker`
process — never inside the web process. This module must be importable
from that separate process too (it's given as `dotted.path.func_name`
to RQ, which imports it fresh in the worker), so it deliberately has no
FastAPI-specific imports, matching the seam `ImportService.process_job`
already left for exactly this move.
"""
from __future__ import annotations

import asyncio
import uuid

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.imports.service import ImportService

logger = get_logger(__name__)


def run_import_job(job_id: str, file_bytes: bytes) -> None:
    """Synchronous — this is what RQ actually calls in the worker
    process, which has no running asyncio event loop of its own. Bridges
    into the existing fully-async ImportService.process_job via
    asyncio.run(), the standard pattern for running async code from a
    sync entry point. Same commit/rollback discipline as the FastAPI
    BackgroundTasks version this replaces: its own independent DB
    session, not the one that created the job."""

    async def _do() -> None:
        async with AsyncSessionLocal() as session:
            try:
                service = ImportService(session)
                await service.process_job(uuid.UUID(job_id), file_bytes)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    try:
        asyncio.run(_do())
    except Exception as exc:
        logger.error("import_job_failed", job_id=job_id, exc_info=exc)
        raise