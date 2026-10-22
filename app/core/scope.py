"""Department scope for Heads of Department.

A HOD sees the teachers, classes and requests of THEIR department only. The administration (and anyone who is not a
HOD) is never narrowed. If a HOD's own staff record has no department, nothing can be narrowed, so they see everything:
fill the department in on the employee record to restrict them.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Roles


def _norm(value: str | None) -> str | None:
    cleaned = " ".join((value or "").split()).lower()
    return cleaned or None


async def hod_department(db: AsyncSession, user) -> str | None:
    """The department (lower-case, trimmed) this user is limited to, or None when they are not limited."""
    roles = set(getattr(user, "roles", []) or [])
    if Roles.ADMIN in roles or Roles.HOD not in roles:
        return None
    from app.employees.models import Employee

    department = (await db.execute(select(Employee.department).where(Employee.user_id == user.id))).scalar_one_or_none()
    return _norm(department)


async def teacher_ids_in(db: AsyncSession, department: str) -> set[uuid.UUID]:
    """Ids of the employees whose department matches (ignoring case and extra spaces)."""
    from app.employees.models import Employee

    wanted = _norm(department)
    if wanted is None:
        return set()
    squeezed = func.lower(func.regexp_replace(func.trim(Employee.department), r"\s+", " ", "g"))
    return set((await db.execute(select(Employee.id).where(Employee.department.is_not(None), squeezed == wanted))).scalars().all())
