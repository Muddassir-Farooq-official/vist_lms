"""What a person may do: the one place that works it out (sign-in, every request, and "who can approve this?").

    rights = everything their roles give
           + their own GRANTs still in date
           - their own DENYs still in date (a DENY wins over every role)
    An Administrator keeps everything their roles give (a DENY never applies to them), and the LOCKED rights (Access
    control, View as) come only with the Administrator role, whatever else is set.

`extra` is what the person holds beyond what their built-in roles come with (a custom role, a right added to a built-in
role, or their own GRANT). Those are the rights that open pages of the administration's portal for someone who is not an
Administrator (the menu then shows only those pages).
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access.catalog import LOCKED
from app.access.models import UserAccessOverride
from app.auth.models import Permission, Role, RolePermission, User, UserRole
from app.auth.permissions import ROLE_PERMISSION_SEED, Roles
from app.core.clock import institute_today


def _in_date(today: date):
    return or_(UserAccessOverride.until.is_(None), UserAccessOverride.until >= today)


async def effective(session: AsyncSession, user_id: uuid.UUID, *, today: date | None = None) -> tuple[list[str], list[str], list[str]]:
    """(roles, rights, extra rights) for one account."""
    today = today or institute_today()
    roles = sorted((await session.execute(select(Role.code).join(UserRole, UserRole.role_id == Role.id)
                                          .where(UserRole.user_id == user_id))).scalars().all())
    from_roles = set((await session.execute(
        select(Permission.code).distinct().join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id).where(UserRole.user_id == user_id))).scalars().all())
    overrides = (await session.execute(select(UserAccessOverride.permission_code, UserAccessOverride.effect)
                                       .where(UserAccessOverride.user_id == user_id, _in_date(today)))).all()
    return combine(roles, from_roles, overrides)


def combine(roles: list[str], from_roles: set[str], overrides) -> tuple[list[str], list[str], list[str]]:
    admin = Roles.ADMIN in roles
    grants = {c for c, e in overrides if e == "GRANT"}
    denies = {c for c, e in overrides if e == "DENY"}
    rights = set(from_roles) | grants
    if not admin:
        rights -= denies
        rights -= LOCKED
    seeded: set[str] = set()
    for r in roles:
        seeded |= set(ROLE_PERMISSION_SEED.get(r, ()))
    extra = set() if admin else rights - seeded
    return roles, sorted(rights), sorted(extra)


async def users_with_permission(session: AsyncSession, code: str, *, exclude=None, active_only: bool = True) -> list[uuid.UUID]:
    """Everyone who holds this right now: through a role or their own GRANT, minus anyone with a DENY (never an
    Administrator's role rights). Used to find who can approve or should be told about something."""
    today = institute_today()
    by_role = select(UserRole.user_id).join(RolePermission, RolePermission.role_id == UserRole.role_id) \
        .join(Permission, Permission.id == RolePermission.permission_id).where(Permission.code == code)
    by_grant = select(UserAccessOverride.user_id).where(UserAccessOverride.permission_code == code, UserAccessOverride.effect == "GRANT",
                                                        _in_date(today))
    ids = set((await session.execute(by_role)).scalars().all()) | set((await session.execute(by_grant)).scalars().all())
    if code in LOCKED:
        ids &= set((await session.execute(select(UserRole.user_id).join(Role, Role.id == UserRole.role_id).where(Role.code == Roles.ADMIN))).scalars().all())
    denied = set((await session.execute(select(UserAccessOverride.user_id).where(
        UserAccessOverride.permission_code == code, UserAccessOverride.effect == "DENY", _in_date(today)))).scalars().all())
    if denied:
        admins = set((await session.execute(select(UserRole.user_id).join(Role, Role.id == UserRole.role_id)
                                            .where(Role.code == Roles.ADMIN, UserRole.user_id.in_(denied)))).scalars().all())
        ids -= denied - admins
    if exclude is not None:
        ids.discard(exclude)
    if active_only and ids:
        ids = set((await session.execute(select(User.id).where(User.id.in_(ids), User.is_active.is_(True)))).scalars().all())
    return sorted(ids, key=str)


async def users_with_any(session: AsyncSession, codes, *, exclude=None) -> list[uuid.UUID]:
    out: set[uuid.UUID] = set()
    for code in codes:
        out |= set(await users_with_permission(session, code, exclude=exclude))
    return sorted(out, key=str)

