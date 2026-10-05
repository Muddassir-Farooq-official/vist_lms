"""Admin dashboard aggregation queries.

Deliberately reads across domains (students, programs, imports, audit) —
that's what a reports module is for. It never writes anything and never
contains business rules; it's pure aggregation of what already exists.

IMPORTANT: this only surfaces data from modules that actually exist in this
build (students, programs, CSV imports, audit log). It does NOT show fee
collection, attendance, or exam widgets — those modules aren't built yet,
and a dashboard showing zeros or fake numbers for non-existent features
would be actively misleading.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.audit.models import AuditLog
from app.imports.models import ImportJob
from app.programs.models import Program
from app.reports.schemas import (
    AdminDashboardResponse,
    ProgramBreakdown,
    RecentActivity,
    RecentImport,
    StudentStatusCounts,
)
from app.students.models import Student, StudentStatus


class ReportsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _student_status_counts(self) -> StudentStatusCounts:
        stmt = select(Student.status, func.count()).group_by(Student.status)
        result = await self.session.execute(stmt)
        counts = {status.value: count for status, count in result.all()}
        return StudentStatusCounts(
            active=counts.get(StudentStatus.ACTIVE.value, 0),
            suspended=counts.get(StudentStatus.SUSPENDED.value, 0),
            graduated=counts.get(StudentStatus.GRADUATED.value, 0),
            dropped=counts.get(StudentStatus.DROPPED.value, 0),
            total=sum(counts.values()),
        )

    async def _program_totals(self) -> tuple[int, int]:
        total = (await self.session.execute(select(func.count()).select_from(Program))).scalar_one()
        active = (
            await self.session.execute(
                select(func.count()).select_from(Program).where(Program.is_active.is_(True))
            )
        ).scalar_one()
        return total, active

    async def _students_by_program(self, limit: int = 10) -> list[ProgramBreakdown]:
        stmt = (
            select(Program.id, Program.code, Program.name, func.count(Student.id))
            .outerjoin(Student, Student.program_id == Program.id)
            .group_by(Program.id, Program.code, Program.name)
            .order_by(func.count(Student.id).desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [
            ProgramBreakdown(program_id=pid, program_code=code, program_name=name, student_count=count)
            for pid, code, name, count in result.all()
        ]

    async def _recent_imports(self, limit: int = 5) -> list[RecentImport]:
        stmt = select(ImportJob).order_by(ImportJob.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return [
            RecentImport(
                id=job.id,
                filename=job.filename,
                status=job.status,
                total_rows=job.total_rows,
                imported_rows=job.imported_rows,
                invalid_rows=job.invalid_rows,
                created_at=job.created_at,
            )
            for job in result.scalars()
        ]

    async def _recent_activity(self, limit: int = 10) -> list[RecentActivity]:
        stmt = (
            select(AuditLog.action, AuditLog.entity_type, User.username, AuditLog.created_at)
            .outerjoin(User, User.id == AuditLog.actor_user_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [
            RecentActivity(action=action, entity_type=entity_type, actor_username=username, created_at=created_at)
            for action, entity_type, username, created_at in result.all()
        ]

    async def get_admin_dashboard(self) -> AdminDashboardResponse:
        status_counts = await self._student_status_counts()
        total_programs, active_programs = await self._program_totals()
        by_program = await self._students_by_program()
        recent_imports = await self._recent_imports()
        recent_activity = await self._recent_activity()

        return AdminDashboardResponse(
            student_status_counts=status_counts,
            total_programs=total_programs,
            active_programs=active_programs,
            students_by_program=by_program,
            recent_imports=recent_imports,
            recent_activity=recent_activity,
        )