"""Extra access roles for a staff member, on top of the one that comes with their job title.

Real institutes double up: a lecturer is also the Exam Controller, an accountant
also runs the library, a second person needs Administrator access. The job
title still decides the MAIN role (and changing the job title still swaps only
that role); this adds or removes the others. Guards:
  - students' role cannot be given to staff;
  - the role that comes from the job title is managed by changing the job title;
  - you cannot remove your own Administrator access, and the last active
    Administrator can never be removed;
  - the person is signed out everywhere so new access applies at their next sign-in.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Role, User, UserRole
from app.auth.permissions import Roles
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError, ValidationAppError

GRANTABLE = (Roles.ADMIN, Roles.PRINCIPAL, Roles.HOD, Roles.TEACHER, Roles.EXAM_CONTROLLER, Roles.ACCOUNTS_OFFICER, Roles.LIBRARIAN,
             Roles.SECURITY_GUARD, Roles.HOSTEL_WARDEN)


class AccessRoleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _employee_user(self, employee_id: uuid.UUID):
        from app.employees.models import Employee, JobRole

        employee = await self.session.get(Employee, employee_id)
        if employee is None:
            raise ResourceNotFoundError("Employee not found.")
        if employee.user_id is None:
            raise BusinessRuleViolationError("This employee has no login account yet.")
        job = await self.session.get(JobRole, employee.job_role_id) if employee.job_role_id else None
        return employee, (job.default_role_code if job else None)

    async def _codes(self, user_id: uuid.UUID) -> list[str]:
        return list((await self.session.execute(
            select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id))).scalars())

    async def get(self, employee_id: uuid.UUID) -> dict:
        employee, main = await self._employee_user(employee_id)
        codes = await self._codes(employee.user_id)
        return {"employee_id": employee.id, "main_role": main, "roles": sorted(codes),
                "extra_roles": sorted(c for c in codes if c != main), "grantable": [c for c in GRANTABLE if c != main]}

    async def set_extra(self, employee_id: uuid.UUID, extra: list[str], *, actor_user_id: uuid.UUID) -> dict:
        employee, main = await self._employee_user(employee_id)
        wanted = {c.strip().upper() for c in extra}
        bad = [c for c in wanted if c not in GRANTABLE]
        if bad:
            raise ValidationAppError(f"These roles cannot be given to staff: {', '.join(sorted(bad))}.")
        wanted.discard(main or "")
        current = set(await self._codes(employee.user_id))
        target = wanted | ({main} if main else set())
        to_add, to_remove = target - current, (current - target) - {Roles.STUDENT}

        if Roles.ADMIN in to_remove:
            if employee.user_id == actor_user_id:
                raise BusinessRuleViolationError("You cannot remove your own Administrator access. Ask another administrator.")
            admins = (await self.session.execute(
                select(User.id).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id)
                .where(Role.code == Roles.ADMIN, User.is_active.is_(True)))).scalars().all()
            if len(set(admins) - {employee.user_id}) == 0:
                raise BusinessRuleViolationError("This is the last active administrator; give someone else Administrator access first.")
        if not to_add and not to_remove:
            return await self.get(employee_id)

        roles = {r.code: r for r in (await self.session.execute(select(Role).where(Role.code.in_(to_add | to_remove)))).scalars()}
        missing = [c for c in to_add if c not in roles]
        if missing:
            raise BusinessRuleViolationError(f"Role(s) {', '.join(missing)} do not exist. Run the permission seed script.")
        for code in to_add:
            self.session.add(UserRole(user_id=employee.user_id, role_id=roles[code].id))
        for code in to_remove:
            link = await self.session.get(UserRole, (employee.user_id, roles[code].id))
            if link is not None:
                await self.session.delete(link)
        await self.session.flush()

        from app.audit.service import AuditService
        from app.auth.repository import AuthRepository

        await AuthRepository(self.session).revoke_all_for_user(employee.user_id)
        await AuditService(self.session).log(actor_user_id=actor_user_id, action="employee.access_roles_changed", entity_type="employee",
                                             entity_id=employee.id, before={"roles": sorted(current)},
                                             after={"roles": sorted(target | (current & {Roles.STUDENT}))})
        return await self.get(employee_id)