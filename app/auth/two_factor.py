"""Two-step sign-in with an authenticator app (Google Authenticator, Microsoft Authenticator, ...).

Who:   every administrator (role ADMIN) must use it while Settings -> Sign-in security -> "Administrators must use
       two-step sign-in" is on (the default). Others are never asked.
How:   after the right password, an administrator without it set up scans a QR code once and types the 6-digit code;
       after that, every sign-in asks for the code, unless this browser was remembered (30 days, a new device always
       asks). Wrong codes count as wrong passwords (the same limits); a code works once.
Lost phone: another administrator resets it (Users -> the person -> Reset two-step sign-in), or, when nobody else
       can, on the server:  docker compose -f docker-compose.prod.yml exec app python scripts/reset_2fa.py <username>
       The person then sets it up again at their next sign-in. Every reset is in the activity log.

The codes follow RFC 6238 (TOTP: HMAC-SHA1, 30-second steps, 6 digits); the secret is kept encrypted (app/core/secretbox).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import secretbox
from app.core.config import settings
from app.core.exceptions import AuthenticationError, BusinessRuleViolationError, RateLimitedError

STEP = 30
DIGITS = 6
DEVICE_COOKIE = "vist_2fa_device"           # not "vist_device": the security check uses that name
CHALLENGE_MINUTES = 10
MAX_CODE_TRIES = 5                                     # per person per 10 minutes


# ---------------------------------------------------------------------------- the codes (RFC 6238)
def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(value).zfill(DIGITS)


def code_now(secret: str, at: float | None = None) -> str:
    return _code_at(secret, int((at if at is not None else time.time()) // STEP))


def matching_step(secret: str, code: str, at: float | None = None) -> int | None:
    """The time step the code belongs to (now, or one step either side for a slow phone clock), or None."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    now = int((at if at is not None else time.time()) // STEP)
    for counter in (now, now - 1, now + 1):
        if hmac.compare_digest(_code_at(secret, counter), code):
            return counter
    return None


def otpauth_uri(secret: str, username: str) -> str:
    issuer = settings.app_name or "VIST LMS"
    return (f"otpauth://totp/{quote(issuer)}:{quote(username)}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def qr_svg(uri: str) -> str:
    import io

    import segno

    out = io.BytesIO()
    segno.make(uri, error="m").save(out, kind="svg", scale=5, border=2, xmldecl=False, svgns=True, dark="#0f172a", light="#ffffff")
    return out.getvalue().decode()


# ---------------------------------------------------------------------------- who is asked
def required_for(roles: list[str]) -> bool:
    from app.auth.permissions import Roles

    return bool(int(getattr(settings, "two_factor_required_for_admins", 1))) and Roles.ADMIN in roles


def _pv(user) -> str:
    """Changes when the password changes, so a challenge cannot outlive a password reset."""
    return hashlib.sha256((user.password_hash or "").encode()).hexdigest()[:16]


def challenge_for(user, mode: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user.id), "type": "2fa", "mode": mode, "pv": _pv(user), "iat": now,
                       "exp": now + timedelta(minutes=CHALLENGE_MINUTES)}, settings.jwt_secret, algorithm=settings.jwt_algorithm)


async def user_from_challenge(session: AsyncSession, token: str, mode: str):
    from app.auth.repository import AuthRepository

    try:
        data = jwt.decode(token or "", settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise AuthenticationError("This step took too long. Please sign in again.") from exc
    if data.get("type") != "2fa" or data.get("mode") != mode:
        raise AuthenticationError("Please sign in again.")
    user = await AuthRepository(session).get_user_by_id(uuid.UUID(data["sub"]))     # with roles loaded, like a normal sign-in
    if user is None or not user.is_active or data.get("pv") != _pv(user):
        raise AuthenticationError("Please sign in again.")
    return user


# ---------------------------------------------------------------------------- remembered devices
def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def device_trusted(session: AsyncSession, user_id: uuid.UUID, raw: str | None) -> bool:
    from app.auth.models import TrustedDevice

    if not raw:
        return False
    d = (await session.execute(select(TrustedDevice).where(TrustedDevice.token_hash == _hash(raw)))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if d is None or d.user_id != user_id or d.revoked_at is not None or d.expires_at <= now:
        return False
    d.last_used_at = now
    return True


async def remember_device(session: AsyncSession, user_id: uuid.UUID, user_agent: str | None) -> str:
    from app.auth.models import TrustedDevice

    raw = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    session.add(TrustedDevice(user_id=user_id, token_hash=_hash(raw), user_agent=(user_agent or "")[:255], created_at=now,
                              expires_at=now + timedelta(days=int(settings.two_factor_remember_days))))
    await session.flush()
    return raw


# ---------------------------------------------------------------------------- checking a code
async def _guard(user) -> None:
    from app.core.redis import get_redis

    if int(await get_redis().get(f"2fa:fail:{user.id}") or 0) >= MAX_CODE_TRIES:
        raise RateLimitedError("Too many wrong codes. Wait 10 minutes and try again.")


async def _wrong(user) -> None:
    from app.core.redis import get_redis

    r = get_redis()
    key = f"2fa:fail:{user.id}"
    if await r.incr(key) == 1:
        await r.expire(key, 600)


async def check_code(user, secret: str, code: str) -> None:
    """Raises unless the code is right and has not been used before."""
    from app.core.redis import get_redis

    await _guard(user)
    step = matching_step(secret, code)
    if step is None:
        await _wrong(user)
        raise AuthenticationError("That code is not right. Check the 6 digits in your authenticator app and try again.")
    if not await get_redis().set(f"2fa:used:{user.id}:{step}", "1", nx=True, ex=STEP * 4):
        raise AuthenticationError("That code was just used. Wait for the next code (it changes every 30 seconds).")
    await get_redis().delete(f"2fa:fail:{user.id}")


# ---------------------------------------------------------------------------- setting up and resetting
def pending_secret(user) -> str:
    """The secret being set up (made once, so reloading the page shows the same QR code)."""
    s = secretbox.open_(user.totp_pending_enc) if user.totp_pending_enc else None
    if not s:
        s = new_secret()
        user.totp_pending_enc = secretbox.seal(s)
    return s


def active_secret(user) -> str | None:
    return secretbox.open_(user.totp_secret_enc) if user.totp_secret_enc else None


def enable(user, secret: str) -> None:
    user.totp_secret_enc = secretbox.seal(secret)
    user.totp_pending_enc = None
    user.totp_enabled_at = datetime.now(timezone.utc)


async def reset(session: AsyncSession, user, *, actor_id: uuid.UUID | None, how: str) -> None:
    """Turn it off for this person (lost phone): they set it up again at the next sign-in. Remembered devices and open
    sessions end. Kept in the activity log."""
    from app.audit.service import AuditService
    from app.auth.models import RefreshToken, TrustedDevice

    now = datetime.now(timezone.utc)
    user.totp_secret_enc = user.totp_pending_enc = None
    user.totp_enabled_at = None
    await session.execute(update(TrustedDevice).where(TrustedDevice.user_id == user.id, TrustedDevice.revoked_at.is_(None)).values(revoked_at=now))
    await session.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
    await AuditService(session).log(actor_user_id=actor_id, action="auth.two_factor_reset", entity_type="user", entity_id=user.id,
                                    metadata={"username": user.username, "how": how})


def status(user, roles: list[str]) -> dict:
    return {"enabled": user.totp_secret_enc is not None, "enabled_at": user.totp_enabled_at, "required": required_for(roles)}


def check_setup_secret_valid(secret: str | None) -> str:
    if not secret:
        raise BusinessRuleViolationError("Start the set-up again.")
    return secret
