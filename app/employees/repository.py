from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import PageParams
from app.core.search import name_search_condition
from app.employees.models import Employee, EmployeeBonus, EmployeeSalaryHistory, EmployeeStatus, JobRole


class JobRoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, job_role_id: uuid.UUID) -> JobRole | None:
        return await self.session.get(JobRole, job_role_id)

    async def get_by_code(self, code: str) -> JobRole | None:
        stmt = select(JobRole).where(func.upper(JobRole.code) == code.upper())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(self, *, active_only: bool = False) -> list[JobRole]:
        stmt = select(JobRole)
        if active_only:
            stmt = stmt.where(JobRole.is_active.is_(True))
        result = await self.session.execute(stmt.order_by(JobRole.title))
        return list(result.scalars())

    async def create(self, job_role: JobRole) -> JobRole:
        self.session.add(job_role)
        await self.session.flush()
        return job_role

    async def delete(self, job_role: JobRole) -> None:
        await self.session.delete(job_role)
        await self.session.flush()


class EmployeeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, employee_id: uuid.UUID) -> Employee | None:
        return await self.session.get(Employee, employee_id)

    async def get_by_cnic(self, cnic: str) -> Employee | None:
        stmt = select(Employee).where(Employee.cnic == cnic)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_user_id(self, user_id: uuid.UUID) -> Employee | None:
        stmt = select(Employee).where(Employee.user_id == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def search(
        self, *, page: PageParams, query: str | None, status: EmployeeStatus | None, job_role_id: uuid.UUID | None,
        role_code: str | None = None,
    ) -> tuple[list[Employee], int]:
        stmt = select(Employee)
        count_stmt = select(func.count()).select_from(Employee)

        if query:
            from sqlalchemy import or_

            cond = or_(
                name_search_condition(Employee.first_name, Employee.last_name, query),
                Employee.employee_code == query,
                Employee.cnic == query,
                Employee.contact_no == query,
            )
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)

        if status:
            stmt = stmt.where(Employee.status == status)
            count_stmt = count_stmt.where(Employee.status == status)

        if job_role_id:
            stmt = stmt.where(Employee.job_role_id == job_role_id)
            count_stmt = count_stmt.where(Employee.job_role_id == job_role_id)

        if role_code:
            # Filters by the employee's actual, current RBAC role
            # assignment (UserRole/Role), not their job title —
            # job_role_id is a title like "Senior Lecturer" and doesn't
            # necessarily match their live role if it was ever manually
            # reassigned. Used by the "assign teacher" search so it only
            # ever suggests people who can actually be assigned (the
            # backend already rejects anyone else; this just stops the
            # UI from offering a choice that would fail).
            from app.auth.models import Role, UserRole

            role_subquery = (
                select(UserRole.user_id)
                .join(Role, UserRole.role_id == Role.id)
                .where(Role.code == role_code)
            )
            stmt = stmt.where(Employee.user_id.in_(role_subquery))
            count_stmt = count_stmt.where(Employee.user_id.in_(role_subquery))

        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(Employee.last_name, Employee.first_name).offset(page.offset).limit(page.page_size)
        result = await self.session.execute(stmt)
        return list(result.scalars()), total

    async def create(self, employee: Employee) -> Employee:
        self.session.add(employee)
        await self.session.flush()
        return employee

    async def get_current_salary(self, employee_id: uuid.UUID) -> EmployeeSalaryHistory | None:
        stmt = select(EmployeeSalaryHistory).where(
            EmployeeSalaryHistory.employee_id == employee_id, EmployeeSalaryHistory.effective_to.is_(None)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_salary_history(self, employee_id: uuid.UUID) -> list[EmployeeSalaryHistory]:
        stmt = (
            select(EmployeeSalaryHistory)
            .where(EmployeeSalaryHistory.employee_id == employee_id)
            .order_by(EmployeeSalaryHistory.effective_from.desc(), EmployeeSalaryHistory.effective_to.is_(None).desc())
        )
        return list((await self.session.execute(stmt)).scalars())

    async def get_bonuses(self, employee_id: uuid.UUID) -> list[EmployeeBonus]:
        stmt = (
            select(EmployeeBonus)
            .where(EmployeeBonus.employee_id == employee_id)
            .order_by(EmployeeBonus.awarded_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars())

    async def count_by_job_role(self, job_role_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(Employee).where(Employee.job_role_id == job_role_id)
        return (await self.session.execute(stmt)).scalar_one()