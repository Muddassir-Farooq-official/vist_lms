"""Running an import job, in its own database session.

Two ways in, same code:
  * `run_import_job` — what RQ calls in the separate `rq worker` process (docker-compose `worker` service). It must stay
    importable without FastAPI (RQ imports it fresh in the worker).
  * `run_in_app` — used by the upload endpoint when no worker is listening (worker container stopped or crashed), so an
    upload is still processed instead of waiting as UPLOADED for ever.
If processing raises, the job is marked FAILED with a plain reason, so it never hangs and the same file can be uploaded
again to retry.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.imports.service import ImportService

logger = get_logger(__name__)


async def process_in_own_session(job_id: uuid.UUID, file_bytes: bytes) -> None:
    # The worker process starts with only the import code loaded; every table must be known before saving
    # (otherwise: NoReferencedTableError, e.g. fee_assignments -> enrollments).
    from app.core.model_registry import load_all_models

    load_all_models()
    async with AsyncSessionLocal() as session:
        try:
            await ImportService(session).process_job(job_id, file_bytes)
            await session.commit()
            return
        except Exception as exc:
            await session.rollback()
            logger.error("import_job_failed", job_id=str(job_id), exc_info=exc)
            error = exc
    await _mark_failed(job_id, f"The import stopped because of a system error ({type(error).__name__}). Nothing was imported. "
                               "Upload the same file again to retry.")
    raise error


async def _mark_failed(job_id: uuid.UUID, reason: str) -> None:
    from app.imports.models import ImportJob, ImportJobStatus

    async with AsyncSessionLocal() as session:
        job = await session.get(ImportJob, job_id)
        if job is not None and job.status not in (ImportJobStatus.COMPLETED.value, ImportJobStatus.FAILED.value):
            job.status, job.failure_reason, job.finished_at = ImportJobStatus.FAILED.value, reason[:500], datetime.now(timezone.utc)
            await session.commit()


def run_import_job(job_id: str, file_bytes: bytes) -> None:
    """Synchronous entry point RQ calls in the worker process (no event loop of its own there)."""
    asyncio.run(process_in_own_session(uuid.UUID(job_id), file_bytes))


async def run_in_app(job_id: uuid.UUID, file_bytes: bytes) -> None:
    """Background task inside the web process (after the response is sent). Errors are already recorded on the job."""
    try:
        await process_in_own_session(job_id, file_bytes)
    except Exception:
        pass