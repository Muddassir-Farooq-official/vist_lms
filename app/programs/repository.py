from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.programs.models import Program


class ProgramRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, program_id: uuid.UUID) -> Program | None:
        return await self.session.get(Program, program_id)

    async def get_by_code(self, code: str) -> Program | None:
        stmt = select(Program).where(func.upper(Program.code) == code.upper())
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_normalized_name(self, name: str) -> list[Program]:
        """Returns ALL matches — the caller (import matcher) must reject
        ambiguity rather than silently picking one (Section 9)."""
        stmt = select(Program).where(func.lower(func.trim(Program.name)) == name.strip().lower())
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def list_all(self, *, active_only: bool = False) -> list[Program]:
        stmt = select(Program)
        if active_only:
            stmt = stmt.where(Program.is_active.is_(True))
        result = await self.session.execute(stmt.order_by(Program.name))
        return list(result.scalars())

    async def create(self, program: Program) -> Program:
        self.session.add(program)
        await self.session.flush()
        return program
