from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Request, Response

from app.auth.redirects import home_for_roles
from app.auth.schemas import ChangePasswordRequest, CurrentUser, LoginRequest, TokenResponse
from app.auth.service import AuthService
from app.core.config import settings
from app.core.security import create_access_token
from app.core.database import get_db
from app.core.dependencies import get_current_user_allow_pending_password_change
from app.core.exceptions import AuthenticationError

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
ACCESS_COOKIE = "access_token"


def _cookie_kwargs() -> dict:
    return dict(
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        domain=settings.cookie_domain or None,
        path="/",
    )


def _set_access_cookie(response: Response, access_token: str) -> None:
    response.set_cookie(
        ACCESS_COOKIE, access_token, max_age=settings.jwt_access_token_expire_minutes * 60, **_cookie_kwargs()
    )


def _set_auth_cookies(response: Response, *, access_token: str, refresh_token: str) -> None:
    _set_access_cookie(response, access_token)
    response.set_cookie(
        REFRESH_COOKIE, refresh_token, max_age=settings.jwt_refresh_token_expire_days * 86400, **_cookie_kwargs()
    )


@router.post("/login", response_model=TokenResponse, summary="Authenticate and receive tokens")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db=Depends(get_db),
) -> TokenResponse:
    """No auth required. Rate-limited per username+IP. Sets HttpOnly cookies
    for browser clients and also returns the access token for API clients."""
    service = AuthService(db)
    user, tokens = await service.login(
        username=payload.username,
        password=payload.password,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    _set_auth_cookies(response, access_token=tokens.access_token, refresh_token=tokens.refresh_token)
    roles, perms = service._roles_and_permissions(user)
    return TokenResponse(
        access_token=tokens.access_token,
        expires_in_minutes=tokens.expires_in_minutes,
        user_id=str(user.id),
        username=user.username,
        roles=roles,
        permissions=list(perms),
        must_change_password=user.must_change_password,
        redirect_to=home_for_roles(roles, user.must_change_password),
    )


@router.post("/refresh", response_model=TokenResponse, summary="Rotate refresh token, issue new access token")
async def refresh(
    request: Request,
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    db=Depends(get_db),
) -> TokenResponse:
    if not refresh_token:
        raise AuthenticationError("No refresh token provided.")
    service = AuthService(db)
    _, tokens = await service.refresh(
        raw_refresh_token=refresh_token,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    _set_auth_cookies(response, access_token=tokens.access_token, refresh_token=tokens.refresh_token)
    return TokenResponse(access_token=tokens.access_token, expires_in_minutes=tokens.expires_in_minutes)


@router.post("/logout", status_code=204, summary="Revoke current refresh token and clear cookies")
async def logout(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    db=Depends(get_db),
) -> None:
    if refresh_token:
        service = AuthService(db)
        await service.logout(raw_refresh_token=refresh_token)
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")


@router.get("/me", response_model=CurrentUser, summary="Get the current authenticated user")
async def me(current_user: CurrentUser = Depends(get_current_user_allow_pending_password_change)) -> CurrentUser:
    return current_user


@router.post("/change-password", status_code=204, summary="Change own password")
async def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    current_user: CurrentUser = Depends(get_current_user_allow_pending_password_change),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    db=Depends(get_db),
) -> None:
    from app.auth.repository import AuthRepository

    service = AuthService(db)
    user = await AuthRepository(db).get_user_by_id(current_user.id)
    if user is None:
        raise AuthenticationError("User not found.")
    await service.change_password(
        user=user, current_password=payload.current_password, new_password=payload.new_password,
        current_refresh_token=refresh_token,
    )
    # The access token in the browser still says must_change_password=true
    # (that flag is baked into the signed token), so without a fresh one the
    # very next page would send a brand-new student straight back to this form
    # until the old token expired. Issue a new access cookie reflecting reality.
    roles, perms = service._roles_and_permissions(user)
    fresh = create_access_token(
        user_id=user.id, username=user.username, roles=roles, permissions=perms,
        must_change_password=user.must_change_password,
    )
    _set_access_cookie(response, fresh)