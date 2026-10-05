from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.auth.models import User
from app.auth.repository import AuthRepository
from app.core.config import settings
from app.core.constants import DEFAULT_STUDENT_PASSWORD
from app.core.exceptions import AuthenticationError, BusinessRuleViolationError, RateLimitedError
from app.core.redis import get_redis
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    refresh_token_expiry,
    verify_password,
)


@dataclass
class AuthTokens:
    access_token: str
    refresh_token: str
    expires_in_minutes: int


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AuthRepository(session)
        self.audit = AuditService(session)

    def _roles_and_permissions(self, user: User) -> tuple[list[str], list[str]]:
        roles = [ur.role.code for ur in user.user_roles]
        perms: set[str] = set()
        for ur in user.user_roles:
            for rp in ur.role.role_permissions:
                perms.add(rp.permission.code)
        return roles, sorted(perms)

    async def _check_rate_limit(self, *, username: str, ip: str | None) -> None:
        redis = get_redis()

        # Per (username, ip) — the original limiter. Tight window, low
        # threshold: catches a single source hammering one account.
        per_ip_key = f"ratelimit:login:{username}:{ip or 'unknown'}"
        per_ip_count = await redis.incr(per_ip_key)
        if per_ip_count == 1:
            await redis.expire(per_ip_key, 60)
        if per_ip_count > settings.rate_limit_login_per_minute:
            raise RateLimitedError("Too many login attempts. Please wait and try again.")

        # Per username ONLY, across a longer window — independent of
        # source IP, so rotating IPs doesn't bypass it. See the
        # settings field's comment for why the threshold differs from
        # the per-IP one above.
        global_key = f"ratelimit:login:global:{username}"
        global_count = await redis.incr(global_key)
        if global_count == 1:
            await redis.expire(global_key, 3600)
        if global_count > settings.rate_limit_login_per_username_per_hour:
            raise RateLimitedError("Too many login attempts for this account. Please wait and try again.")

    async def _deactivation_reason(self, user: User) -> str:
        """Only ever called after the password has already been
        confirmed correct — see the ordering note in login() above.
        Deliberately specific rather than a generic 'account disabled':
        a suspended/dropped/graduated student or a terminated employee
        needs to know why, not just that something's wrong, and this is
        an internal institutional system, not a public consumer app
        where confirming account state to a verified credential holder
        is a meaningful information-leak concern."""
        from app.employees.models import Employee, EmployeeStatus
        from app.students.models import Student, StudentStatus

        student = (
            await self.session.execute(select(Student).where(Student.user_id == user.id))
        ).scalar_one_or_none()
        if student is not None:
            if student.status == StudentStatus.SUSPENDED:
                return "Your account has been suspended. Contact the office for details."
            if student.status == StudentStatus.DROPPED:
                return "Your account has been deactivated because your enrollment was dropped."
            if student.status == StudentStatus.GRADUATED:
                return "Your account has been deactivated because you have graduated."

        employee = (
            await self.session.execute(select(Employee).where(Employee.user_id == user.id))
        ).scalar_one_or_none()
        if employee is not None and employee.status == EmployeeStatus.TERMINATED:
            return "Your account has been deactivated. Contact the office for details."

        return "Your account has been deactivated. Contact the office for details."

    async def login(
        self, *, username: str, password: str, ip: str | None, user_agent: str | None
    ) -> tuple[User, AuthTokens]:
        await self._check_rate_limit(username=username, ip=ip)

        user = await self.repo.get_user_by_username(username)
        # Password checked BEFORE the is_active check, deliberately —
        # giving a specific "your account is suspended" reason is only
        # safe once we already know the password was correct. Checking
        # is_active first (the previous behavior) would let someone
        # confirm a username exists and is deactivated without ever
        # having the right password, which is exactly the information
        # leak this ordering avoids.
        password_ok = bool(user) and verify_password(password, user.password_hash)

        await self.repo.record_login_attempt(
            username_attempted=username,
            user_id=user.id if user else None,
            success=password_ok and bool(user) and user.is_active,
            ip_address=ip,
            user_agent=user_agent,
        )

        if not password_ok or user is None:
            await self.audit.log(
                actor_user_id=None,
                action="auth.login_failed",
                entity_type="user",
                entity_id=None,
                metadata={"username": username},
                ip=ip,
                user_agent=user_agent,
            )
            raise AuthenticationError("Invalid username or password.")

        if not user.is_active:
            reason = await self._deactivation_reason(user)
            await self.audit.log(
                actor_user_id=user.id,
                action="auth.login_blocked_inactive",
                entity_type="user",
                entity_id=user.id,
                metadata={"username": username},
                ip=ip,
                user_agent=user_agent,
            )
            raise AuthenticationError(reason)

        # Opportunistic rehash-on-login: this is the only place the
        # plaintext password is ever available, so it's the only place
        # a hash can be transparently upgraded to the hasher's current
        # parameters (e.g. if PasswordHasher's time/memory cost is
        # tightened later). needs_rehash() has existed since this
        # module was written but was never actually called anywhere —
        # meaning no existing user's hash could ever have been
        # upgraded even if the parameters had changed. Placed after the
        # is_active check so a suspended/deactivated account's hash is
        # never touched.
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)

        roles, perms = self._roles_and_permissions(user)
        access_token = create_access_token(
            user_id=user.id,
            username=user.username,
            roles=roles,
            permissions=perms,
            must_change_password=user.must_change_password,
        )

        raw_refresh = generate_refresh_token()
        family_id = uuid.uuid4()
        await self.repo.create_refresh_token(
            user_id=user.id,
            token_hash=hash_refresh_token(raw_refresh),
            family_id=family_id,
            expires_at=refresh_token_expiry(),
            user_agent=user_agent,
            ip_address=ip,
        )

        from datetime import datetime, timezone

        user.last_login_at = datetime.now(timezone.utc)

        await self.audit.log(
            actor_user_id=user.id,
            action="auth.login_success",
            entity_type="user",
            entity_id=user.id,
            ip=ip,
            user_agent=user_agent,
        )

        return user, AuthTokens(
            access_token=access_token,
            refresh_token=raw_refresh,
            expires_in_minutes=settings.jwt_access_token_expire_minutes,
        )

    async def refresh(
        self, *, raw_refresh_token: str, ip: str | None, user_agent: str | None
    ) -> tuple[User, AuthTokens]:
        token_hash = hash_refresh_token(raw_refresh_token)
        existing = await self.repo.get_refresh_token_by_hash(token_hash)

        if existing is None:
            raise AuthenticationError("Invalid refresh token.")

        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)

        if existing.revoked_at is not None:
            # Reuse of an already-rotated/revoked token — treat as a stolen
            # token scenario and kill the whole family (Section 21).
            await self.repo.revoke_token_family(existing.family_id)
            await self.audit.log(
                actor_user_id=existing.user_id,
                action="auth.refresh_reuse_detected",
                entity_type="refresh_token",
                entity_id=existing.id,
                ip=ip,
                user_agent=user_agent,
            )
            raise AuthenticationError("Refresh token has been revoked.")

        if existing.expires_at < now:
            raise AuthenticationError("Refresh token has expired.")

        user = await self.repo.get_user_by_id(existing.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Account is not active.")

        # Rotate: issue a new token in the same family, revoke the old one.
        raw_new = generate_refresh_token()
        new_token = await self.repo.create_refresh_token(
            user_id=user.id,
            token_hash=hash_refresh_token(raw_new),
            family_id=existing.family_id,
            expires_at=refresh_token_expiry(),
            user_agent=user_agent,
            ip_address=ip,
        )
        await self.repo.revoke_token(existing, replaced_by_id=new_token.id)

        roles, perms = self._roles_and_permissions(user)
        access_token = create_access_token(
            user_id=user.id,
            username=user.username,
            roles=roles,
            permissions=perms,
            must_change_password=user.must_change_password,
        )

        return user, AuthTokens(
            access_token=access_token,
            refresh_token=raw_new,
            expires_in_minutes=settings.jwt_access_token_expire_minutes,
        )

    async def logout(self, *, raw_refresh_token: str) -> None:
        token_hash = hash_refresh_token(raw_refresh_token)
        existing = await self.repo.get_refresh_token_by_hash(token_hash)
        if existing is not None and existing.revoked_at is None:
            await self.repo.revoke_token(existing)

    async def change_password(
        self, *, user: User, current_password: str, new_password: str, current_refresh_token: str | None = None,
    ) -> None:
        if not verify_password(current_password, user.password_hash):
            raise AuthenticationError("Current password is incorrect.")
        if verify_password(new_password, user.password_hash):
            raise BusinessRuleViolationError("Choose a password different from your current one.")
        if new_password.strip().lower() in {DEFAULT_STUDENT_PASSWORD.lower(), (user.username or "").lower()}:
            raise BusinessRuleViolationError("That password is too easy to guess. Choose something only you know.")
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        # End every OTHER session (e.g. a stolen one), but keep this one
        # alive — the forced first-login change continues straight into
        # the dashboard on the same session.
        await self.repo.revoke_all_for_user(
            user.id,
            except_token_hash=hash_refresh_token(current_refresh_token) if current_refresh_token else None,
        )
        await self.audit.log(
            actor_user_id=user.id,
            action="auth.password_changed",
            entity_type="user",
            entity_id=user.id,
        )