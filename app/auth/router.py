from __future__ import annotations

from app.core.net import client_ip
from pydantic import BaseModel, Field
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
    device: str | None = Cookie(default=None, alias="vist_2fa_device"),
) -> TokenResponse:
    """No auth required. Rate-limited per username+IP. Sets HttpOnly cookies
    for browser clients and also returns the access token for API clients.
    Administrators (two-step sign-in, app/auth/two_factor.py): unless this browser is remembered, nothing is issued
    yet; the answer says two_factor = "code" (type the code) or "setup" (scan the QR code first) with a challenge."""
    from app.auth import two_factor as tf

    service = AuthService(db)
    ip, ua = client_ip(request), request.headers.get("user-agent")
    user = await service.check_password(username=payload.username, password=payload.password, ip=ip, user_agent=ua)
    roles, _perms = service._roles_and_permissions(user)
    if tf.required_for(roles):
        if user.totp_secret_enc is None:
            return TokenResponse(access_token="", expires_in_minutes=0, two_factor="setup", challenge=tf.challenge_for(user, "setup"),
                                 remember_days=int(settings.two_factor_remember_days))
        if not await tf.device_trusted(db, user.id, device):
            return TokenResponse(access_token="", expires_in_minutes=0, two_factor="code", challenge=tf.challenge_for(user, "code"),
                                 remember_days=int(settings.two_factor_remember_days))
    tokens = await service.start_session(user, ip=ip, user_agent=ua)
    return await _signed_in(db, response, service, user, tokens)


async def _signed_in(db, response: Response, service: AuthService, user, tokens) -> TokenResponse:
    """Cookies + the answer the login page needs to go straight to the right portal."""
    _set_auth_cookies(response, access_token=tokens.access_token, refresh_token=tokens.refresh_token)
    roles, perms = service._roles_and_permissions(user)
    from app.core.dependencies import email_gate_on

    must_verify = not (user.email and user.email_verified_at) and await email_gate_on(db)
    return TokenResponse(
        access_token=tokens.access_token,
        expires_in_minutes=tokens.expires_in_minutes,
        user_id=str(user.id),
        username=user.username,
        roles=roles,
        permissions=list(perms),
        must_change_password=user.must_change_password,
        email_verification_required=must_verify,
        redirect_to=home_for_roles(roles, user.must_change_password, must_verify),
    )


class _TwoFactorCodeIn(BaseModel):
    challenge: str = Field(min_length=10, max_length=2000)
    code: str = Field(min_length=6, max_length=12)
    remember: bool = False


class _TwoFactorStartIn(BaseModel):
    challenge: str = Field(min_length=10, max_length=2000)


def _remember_cookie(response: Response, raw: str) -> None:
    from app.auth.two_factor import DEVICE_COOKIE

    response.set_cookie(DEVICE_COOKIE, raw, max_age=int(settings.two_factor_remember_days) * 86400, **_cookie_kwargs())


@router.post("/2fa/verify", response_model=TokenResponse, summary="Second step: the 6-digit code from the authenticator app")
async def two_factor_verify(payload: _TwoFactorCodeIn, request: Request, response: Response, db=Depends(get_db)) -> TokenResponse:
    from app.audit.service import AuditService
    from app.auth import two_factor as tf

    user = await tf.user_from_challenge(db, payload.challenge, "code")
    secret = tf.active_secret(user)
    if not secret:
        raise AuthenticationError("Two-step sign-in was reset for this account. Please sign in again to set it up.")
    try:
        await tf.check_code(user, secret, payload.code)
    except AuthenticationError:
        from app.core.side_log import committed_now

        async with committed_now(db) as side:
            await AuditService(side).log(actor_user_id=user.id, action="auth.two_factor_failed", entity_type="user", entity_id=user.id,
                                         ip=client_ip(request), user_agent=request.headers.get("user-agent"))
        raise
    if payload.remember:
        _remember_cookie(response, await tf.remember_device(db, user.id, request.headers.get("user-agent")))
    service = AuthService(db)
    tokens = await service.start_session(user, ip=client_ip(request), user_agent=request.headers.get("user-agent"))
    return await _signed_in(db, response, service, user, tokens)


@router.post("/2fa/setup", summary="Set-up step 1: the QR code (and the key, to type in by hand) for the authenticator app")
async def two_factor_setup(payload: _TwoFactorStartIn, db=Depends(get_db)) -> dict:
    from app.auth import two_factor as tf

    user = await tf.user_from_challenge(db, payload.challenge, "setup")
    secret = tf.pending_secret(user)
    uri = tf.otpauth_uri(secret, user.username)
    return {"qr_svg": tf.qr_svg(uri), "key": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)), "account": user.username,
            "issuer": settings.app_name}


@router.post("/2fa/setup/confirm", response_model=TokenResponse, summary="Set-up step 2: the first code turns two-step sign-in on and signs in")
async def two_factor_setup_confirm(payload: _TwoFactorCodeIn, request: Request, response: Response, db=Depends(get_db)) -> TokenResponse:
    from app.audit.service import AuditService
    from app.auth import two_factor as tf

    user = await tf.user_from_challenge(db, payload.challenge, "setup")
    if user.totp_secret_enc is not None:
        raise AuthenticationError("Two-step sign-in is already set up. Please sign in again.")
    secret = tf.check_setup_secret_valid(tf.pending_secret(user) if user.totp_pending_enc else None)
    await tf.check_code(user, secret, payload.code)
    tf.enable(user, secret)
    await AuditService(db).log(actor_user_id=user.id, action="auth.two_factor_enabled", entity_type="user", entity_id=user.id,
                               ip=client_ip(request), user_agent=request.headers.get("user-agent"))
    if payload.remember:
        _remember_cookie(response, await tf.remember_device(db, user.id, request.headers.get("user-agent")))
    service = AuthService(db)
    tokens = await service.start_session(user, ip=client_ip(request), user_agent=request.headers.get("user-agent"))
    return await _signed_in(db, response, service, user, tokens)


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
        ip=client_ip(request),
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


class _ForgotIn(BaseModel):
    username: str = Field(min_length=1, max_length=60)


class _ResetIn(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    code: str = Field(min_length=4, max_length=10)
    new_password: str = Field(min_length=10, max_length=256)


@router.post("/forgot-password", summary="Send a 6-digit reset code by SMS to the mobile on the person's record (same answer whoever asks)")
async def forgot_password(payload: _ForgotIn, request: Request, db=Depends(get_db)) -> dict:
    from app.auth.reset import PasswordResetService

    return await PasswordResetService(db).request(payload.username, ip=client_ip(request))


@router.post("/reset-password", status_code=204, summary="Set a new password with the SMS code; signs the account out everywhere")
async def reset_password(payload: _ResetIn, request: Request, db=Depends(get_db)) -> None:
    from app.auth.reset import PasswordResetService

    await PasswordResetService(db).perform(payload.username, payload.code, payload.new_password, ip=client_ip(request))


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

# ------------------------------------------------------------------ prove an email address (one-time code)
class _EmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class _CodeIn(BaseModel):
    code: str = Field(min_length=4, max_length=12)


@router.get("/email", summary="My email address, whether it is verified, and whether I must verify before using the portal")
async def my_email_status(current_user: CurrentUser = Depends(get_current_user_allow_pending_password_change), db=Depends(get_db)) -> dict:
    from app.auth.email_verify import EmailVerificationService

    return await EmailVerificationService(db).status(current_user.id)


@router.post("/email/send-code", summary="Email a 6-digit code to this address (works for 10 minutes)")
async def send_email_code(payload: _EmailIn, request: Request,
                          current_user: CurrentUser = Depends(get_current_user_allow_pending_password_change), db=Depends(get_db)) -> dict:
    from app.auth.email_verify import EmailVerificationService

    _no_viewer(current_user)
    return await EmailVerificationService(db).send_code(current_user.id, payload.email, ip=client_ip(request))


@router.post("/email/verify", summary="Confirm the address with the code; the portal opens straight away")
async def verify_email_code(payload: _CodeIn, request: Request,
                            current_user: CurrentUser = Depends(get_current_user_allow_pending_password_change), db=Depends(get_db)) -> dict:
    from app.auth.email_verify import EmailVerificationService

    _no_viewer(current_user)
    out = await EmailVerificationService(db).verify(current_user.id, payload.code, ip=client_ip(request))
    out["redirect_to"] = home_for_roles(current_user.roles, current_user.must_change_password) or "/"
    return out


def _no_viewer(current_user: CurrentUser) -> None:
    from app.core.exceptions import AuthorizationError

    if current_user.viewing_as_by_id:
        raise AuthorizationError("You are viewing someone else's screens; their email cannot be changed from here.")