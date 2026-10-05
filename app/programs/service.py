from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.exceptions import DuplicateResourceError, ResourceNotFoundError
from app.programs.models import Program
from app.programs.repository import ProgramRepository
from app.programs.schemas import ProgramCreate, ProgramUpdate


class ProgramService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ProgramRepository(session)
        self.audit = AuditService(session)

    async def create_program(self, data: ProgramCreate, *, actor_id: uuid.UUID) -> Program:
        if await self.repo.get_by_code(data.code):
            raise DuplicateResourceError(f"Program code '{data.code}' already exists.")
        if await self.repo.get_by_normalized_name(data.name):
            raise DuplicateResourceError(f"Program name '{data.name}' already exists.")

        program = Program(
            code=data.code.strip().upper(),
            name=data.name.strip(),
            duration_years=data.duration_years,
            total_semesters=data.total_semesters,
            max_credit_hours_per_semester=data.max_credit_hours_per_semester,
        )
        await self.repo.create(program)
        await self.audit.log(
            actor_user_id=actor_id,
            action="program.created",
            entity_type="program",
            entity_id=program.id,
            after={"code": program.code, "name": program.name},
        )
        return program

    async def get_program(self, program_id: uuid.UUID) -> Program:
        program = await self.repo.get_by_id(program_id)
        if program is None:
            raise ResourceNotFoundError("Program not found.")
        return program

    async def list_programs(self, *, active_only: bool = False) -> list[Program]:
        return await self.repo.list_all(active_only=active_only)

    async def update_program(
        self, program_id: uuid.UUID, data: ProgramUpdate, *, actor_id: uuid.UUID
    ) -> Program:
        program = await self.get_program(program_id)
        before = {
            "name": program.name,
            "duration_years": program.duration_years,
            "total_semesters": program.total_semesters,
            "is_active": program.is_active,
            "max_credit_hours_per_semester": program.max_credit_hours_per_semester,
        }
        if data.name is not None:
            program.name = data.name.strip()
        if data.duration_years is not None:
            program.duration_years = data.duration_years
        if data.total_semesters is not None:
            program.total_semesters = data.total_semesters
        if data.is_active is not None:
            program.is_active = data.is_active
        if data.max_credit_hours_per_semester is not None:
            program.max_credit_hours_per_semester = data.max_credit_hours_per_semester

        await self.audit.log(
            actor_user_id=actor_id,
            action="program.updated",
            entity_type="program",
            entity_id=program.id,
            before=before,
            after={
                "name": program.name,
                "duration_years": program.duration_years,
                "total_semesters": program.total_semesters,
                "is_active": program.is_active,
                "max_credit_hours_per_semester": program.max_credit_hours_per_semester,
            },
        )
        return program