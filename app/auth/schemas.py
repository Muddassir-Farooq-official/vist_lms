from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    """Returned to API clients. Browser (Jinja) flow instead receives cookies
    and does not see the raw tokens in the response body."""

    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    # Login-only extras so the browser can go straight to the right page with
    # ONE request (it used to make a second /auth/me call just to read these).
    # None on token refresh.
    user_id: str | None = None
    username: str | None = None
    roles: list[str] | None = None
    permissions: list[str] | None = None
    must_change_password: bool | None = None
    email_verification_required: bool | None = None
    redirect_to: str | None = None
    # Two-step sign-in: "code" or "setup" when the password was right but the code is still needed (nothing issued yet).
    two_factor: str | None = None
    challenge: str | None = None
    remember_days: int | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=10, max_length=256)


class RequestPasswordReset(BaseModel):
    username: str


class PerformPasswordReset(BaseModel):
    token: str
    new_password: str = Field(min_length=10, max_length=256)


class CurrentUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    roles: list[str]
    permissions: list[str]
    must_change_password: bool
    # The administrator requires a verified email and this account has none yet: only /verify-email works until it does.
    email_verification_required: bool = False
    # Set when an administrator is looking at this person's screens through "view as": who is looking.
    # Such a session is read-only (see app/auth/view_as.py and the guard in app/main.py).
    viewing_as_by: str | None = None
    viewing_as_by_id: uuid.UUID | None = None