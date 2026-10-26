"""Shared dependencies: authenticate the request, then authorize it.

Dependency chain per protected route:
    get_current_user  ->  require_permission("x.y")  ->  route body
Resource-scope checks (e.g. "is this teacher assigned to this section")
happen inside each domain's own service layer using the CurrentUser object,
since only that domain knows how to resolve its own resource ownership.
"""
from __future__ import annotations

import time
import uuid

import jwt
from fastapi import Cookie, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import CurrentUser
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.security import decode_access_token


# user id -> (checked_at, is_active, must_change_password, roles, permissions). A signed token alone would let a
# deactivated, terminated or re-roled person keep working until it expires, so the account is re-read from the
# database at most every `session_check_seconds` and what it says there wins over what the token claims.
_session_ok: dict[str, tuple[float, bool, bool, list[str], list[str]]] = {}


async def _fresh_account(db: AsyncSession, user_id: str) -> tuple[bool, bool, list[str], list[str]] | None:
    from app.auth.models import Permission, Role, RolePermission, User, UserRole

    try:
        uid = uuid.UUID(str(user_id))
    except ValueError:
        return None
    row = (await db.execute(select(User.is_active, User.must_change_password).where(User.id == uid))).one_or_none()
    if row is None:
        return None
    roles = sorted((await db.execute(select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == uid))).scalars().all())
    perms = sorted((await db.execute(
        select(Permission.code).distinct().join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id).where(UserRole.user_id == uid))).scalars().all())
    return bool(row[0]), bool(row[1]), roles, perms


async def _decode_current_user(
    access_token_cookie: str | None, authorization: str | None, db: AsyncSession
) -> CurrentUser:
    """Shared decode logic for both variants below — accepts either the
    HttpOnly cookie (browser/Jinja flow) or a Bearer header (API
    clients). Cookie takes precedence when both are present."""
    token = access_token_cookie
    if token is None and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]

    if not token:
        raise AuthenticationError("Not authenticated.")

    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Session expired. Please log in again.") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid session.") from exc

    user_id = str(payload["sub"])
    now = time.monotonic()
    cached = _session_ok.get(user_id)
    ttl = max(int(settings.session_check_seconds or 0), 0)
    if cached is None or ttl == 0 or now - cached[0] >= ttl:
        fresh = await _fresh_account(db, user_id)
        if fresh is None or not fresh[0]:
            _session_ok.pop(user_id, None)
            raise AuthenticationError("This account is no longer active. Please contact the office.")
        cached = (now, *fresh)
        if len(_session_ok) > 5000:
            _session_ok.clear()
        _session_ok[user_id] = cached
    _, _, must_change, roles, perms = cached

    viewer = payload.get("va")
    return CurrentUser(
        id=payload["sub"],
        username=payload.get("username", ""),
        roles=roles,
        permissions=perms,
        # An administrator viewing someone's screens is never sent to that person's change-password form.
        must_change_password=False if viewer else must_change,
        viewing_as_by=payload.get("va_name") if viewer else None,
        viewing_as_by_id=viewer,
    )


async def get_current_user_allow_pending_password_change(
    access_token_cookie: str | None = Cookie(default=None, alias="access_token"),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """Identical to get_current_user below, EXCEPT it does not block a
    must_change_password account. Use this ONLY for the handful of
    endpoints that must stay reachable before a fresh account's
    password is changed: POST /auth/change-password itself, GET
    /auth/me (so the frontend can show 'please change your password'
    instead of erroring), and POST /auth/logout (a locked-out user
    should still be able to log out cleanly). Every other protected
    endpoint should keep using get_current_user, which enforces the
    block — see that function's docstring for why this split exists."""
    return await _decode_current_user(access_token_cookie, authorization, db)


async def get_current_user(
    access_token_cookie: str | None = Cookie(default=None, alias="access_token"),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """Real security fix, not a new feature: must_change_password has
    existed on the User model and in the JWT payload since early in
    this project, but nothing ever actually enforced it — a fresh
    account (issued a fixed, documented default password specifically
    BECAUSE must_change_password limits the exposure window) could call
    any endpoint its role permits without ever being forced to change
    that password first. This was explicitly flagged as a known gap in
    app/core/constants.py. Now: any account with must_change_password
    still True can reach NOTHING through this dependency except the
    three endpoints that use get_current_user_allow_pending_password_change
    instead (see that function's docstring)."""
    current_user = await _decode_current_user(access_token_cookie, authorization, db)
    if current_user.must_change_password:
        raise AuthorizationError(
            "You must change your password before accessing this. Use POST /auth/change-password."
        )
    return current_user


def require_permission(permission_code: str):
    """Dependency factory: `Depends(require_permission(Permissions.STUDENT_IMPORT))`."""

    async def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if permission_code not in current_user.permissions:
            raise AuthorizationError(f"Missing required permission: {permission_code}")
        return current_user

    return _check


def require_any_permission(*permission_codes: str):
    """Allow a request that holds ANY of these permissions (e.g. the administration, or the exam department for result work)."""

    async def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not set(permission_codes) & set(current_user.permissions):
            raise AuthorizationError(f"Missing required permission: {permission_codes[0]}")
        return current_user

    return _check


def require_any_role(*role_codes: str):
    async def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not set(role_codes) & set(current_user.roles):
            raise AuthorizationError("You do not have access to this resource.")
        return current_user

    return _check


DbSession = Depends(get_db)