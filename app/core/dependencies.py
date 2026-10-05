"""Shared dependencies: authenticate the request, then authorize it.

Dependency chain per protected route:
    get_current_user  ->  require_permission("x.y")  ->  route body
Resource-scope checks (e.g. "is this teacher assigned to this section")
happen inside each domain's own service layer using the CurrentUser object,
since only that domain knows how to resolve its own resource ownership.
"""
from __future__ import annotations

import jwt
from fastapi import Cookie, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.security import decode_access_token


async def _decode_current_user(
    access_token_cookie: str | None, authorization: str | None
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

    return CurrentUser(
        id=payload["sub"],
        username=payload.get("username", ""),
        roles=payload.get("roles", []),
        permissions=payload.get("perms", []),
        must_change_password=payload.get("must_change_password", False),
    )


async def get_current_user_allow_pending_password_change(
    access_token_cookie: str | None = Cookie(default=None, alias="access_token"),
    authorization: str | None = Header(default=None),
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
    return await _decode_current_user(access_token_cookie, authorization)


async def get_current_user(
    access_token_cookie: str | None = Cookie(default=None, alias="access_token"),
    authorization: str | None = Header(default=None),
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
    current_user = await _decode_current_user(access_token_cookie, authorization)
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