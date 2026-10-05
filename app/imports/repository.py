from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.imports.models import ImportJob, ImportRowError


class ImportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_checksum(self, checksum: str) -> ImportJob | None:
        stmt = select(ImportJob).where(ImportJob.file_checksum == checksum)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, job_id: uuid.UUID) -> ImportJob | None:
        return await self.session.get(ImportJob, job_id)

    async def create(self, job: ImportJob) -> ImportJob:
        self.session.add(job)
        await self.session.flush()
        return job

    async def add_row_errors(self, errors: list[ImportRowError]) -> None:
        self.session.add_all(errors)
        await self.session.flush()

    async def list_row_errors(self, job_id: uuid.UUID, *, offset: int, limit: int) -> tuple[list[ImportRowError], int]:
        from sqlalchemy import func

        count_stmt = select(func.count()).select_from(ImportRowError).where(ImportRowError.import_job_id == job_id)
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(ImportRowError)
            .where(ImportRowError.import_job_id == job_id)
            .order_by(ImportRowError.row_number)
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars()), total
