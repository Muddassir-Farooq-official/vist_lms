"""Separation-of-duties helpers: nobody approves their own work, and the institute can never be left without an administrator."""
from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Permission, Role, RolePermission, User, UserRole
from app.auth.permissions import Roles
from app.core.exceptions import BusinessRuleViolationError


def refuse_self(actor_id, other_id, message: str) -> None:
    """Raise `message` when the person acting is the same person who made the thing being decided."""
    if actor_id is not None and other_id is not None and str(actor_id) == str(other_id):
        raise BusinessRuleViolationError(message)


async def ensure_another_admin(session: AsyncSession, user_id: uuid.UUID) -> None:
    """If `user_id` is an active administrator, make sure at least one OTHER active administrator remains."""
    admins = set((await session.execute(
        select(User.id).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id)
        .where(Role.code == Roles.ADMIN, User.is_active.is_(True)))).scalars().all())
    if user_id in admins and not (admins - {user_id}):
        raise BusinessRuleViolationError("This is the last active administrator; give someone else Administrator access first.")


async def four_eyes(session: AsyncSession, actor_id, made_by: Iterable, permission: str, message: str) -> bool:
    """Whoever did the work should not approve it.

    Returns False when the actor did not do the work. If the actor did, another active person holding `permission`
    must decide instead (raises `message`); when the actor is the ONLY such person, they may proceed and True is
    returned so the caller can record that it was a sole-approver decision (work must never get stuck).
    """
    if actor_id is None or str(actor_id) not in {str(m) for m in made_by if m is not None}:
        return False
    others = (await session.execute(
        select(func.count(func.distinct(User.id)))
        .join(UserRole, UserRole.user_id == User.id).join(RolePermission, RolePermission.role_id == UserRole.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(Permission.code == permission, User.is_active.is_(True), User.id != actor_id))).scalar_one()
    if others:
        raise BusinessRuleViolationError(message)
    return True
