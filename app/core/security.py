"""Password hashing (Argon2id) and JWT issuance/verification.

Access tokens are short-lived and stateless. Refresh tokens are opaque
random strings whose HASH is stored server-side (never the raw token),
support rotation, and can be revoked individually or by "family" if reuse
of an already-rotated token is detected (Section 21).
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings

_hasher = PasswordHasher()


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def hash_password(plain_password: str) -> str:
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain_password)
    except VerifyMismatchError:
        return False
    except Exception:
        # Malformed hash, etc. — never raise into the auth flow; just fail closed.
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# ---------------------------------------------------------------------------
# JWT access tokens
# ---------------------------------------------------------------------------
def create_access_token(
    *,
    user_id: uuid.UUID,
    username: str,
    roles: list[str],
    permissions: list[str],
    must_change_password: bool,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "username": username,
        "roles": roles,
        "perms": permissions,
        "must_change_password": must_change_password,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_token_expire_minutes),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError subclasses on invalid/expired tokens."""
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Not an access token")
    return payload


# ---------------------------------------------------------------------------
# Refresh tokens (opaque, hashed at rest)
# ---------------------------------------------------------------------------
def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(raw_token: str) -> str:
    # SHA-256 is sufficient here: the token itself already has 48 bytes of
    # entropy (unlike a user password), so we don't need Argon2's slow-hash
    # properties — we need a fast, deterministic lookup key.
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def refresh_token_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_token_expire_days)


def generate_activation_or_reset_token() -> tuple[str, str]:
    """Returns (raw_token_to_send_to_user, hash_to_store_in_db)."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_refresh_token(raw)