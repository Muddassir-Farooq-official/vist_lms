from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.models import LoginAttempt, RefreshToken, Role, RolePermission, User, UserRole


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    _EAGER_ROLE_PERMS = selectinload(User.user_roles).selectinload(UserRole.role).selectinload(
        Role.role_permissions
    ).selectinload(RolePermission.permission)

    async def get_user_by_username(self, username: str) -> User | None:
        stmt = select(User).where(User.username == username).options(self._EAGER_ROLE_PERMS)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: uuid.UUID) -> User | None:
        stmt = select(User).where(User.id == user_id).options(self._EAGER_ROLE_PERMS)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def record_login_attempt(
        self,
        *,
        username_attempted: str,
        user_id: uuid.UUID | None,
        success: bool,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        import ipaddress

        try:
            ip_address = str(ipaddress.ip_address(ip_address)) if ip_address else None
        except ValueError:
            ip_address = None                      # "unknown" or a broken value: the INET column would refuse it
        self.session.add(
            LoginAttempt(
                username_attempted=username_attempted[:64],
                user_id=user_id,
                success=success,
                ip_address=ip_address,
                user_agent=user_agent,
                created_at=datetime.now(timezone.utc),
            )
        )

    async def create_refresh_token(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        family_id: uuid.UUID,
        expires_at: datetime,
        user_agent: str | None,
        ip_address: str | None,
    ) -> RefreshToken:
        token = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            family_id=family_id,
            created_at=datetime.now(timezone.utc),
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        self.session.add(token)
        await self.session.flush()
        return token

    async def get_refresh_token_by_hash(self, token_hash: str) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def revoke_token_family(self, family_id: uuid.UUID) -> None:
        stmt = select(RefreshToken).where(
            RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None)
        )
        result = await self.session.execute(stmt)
        now = datetime.now(timezone.utc)
        for token in result.scalars():
            token.revoked_at = now

    async def revoke_all_for_user(self, user_id: uuid.UUID, *, except_token_hash: str | None = None) -> None:
        """Kill every still-live refresh token this user holds, so a
        password change/reset actually ends other sessions (a stolen
        session used to survive it indefinitely, since refresh only
        ever checked is_active). except_token_hash spares the caller's
        own current session on a self-service change."""
        stmt = select(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        if except_token_hash is not None:
            stmt = stmt.where(RefreshToken.token_hash != except_token_hash)
        now = datetime.now(timezone.utc)
        for token in (await self.session.execute(stmt)).scalars():
            token.revoked_at = now

    async def revoke_token(self, token: RefreshToken, *, replaced_by_id: uuid.UUID | None = None) -> None:
        token.revoked_at = datetime.now(timezone.utc)
        token.replaced_by_id = replaced_by_id