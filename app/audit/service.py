from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog


class AuditService:
    """Write-only audit trail (Section 29). Callers must never pass password
    hashes, tokens, or other secrets in `before`/`after`/`metadata`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def log(
        self,
        *,
        actor_user_id: uuid.UUID | None,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID | None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.session.add(
            AuditLog(
                actor_user_id=actor_user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                before=before,
                after=after,
                log_metadata=metadata,
                ip_address=ip,
                user_agent=user_agent,
            )
        )
        await self.session.flush()
        # tell the person the change is about (app/notifications/changes.py); a no-op for most actions, never raises
        from app.notifications.changes import RULES, after_audit

        if action in RULES:
            await after_audit(self.session, action=action, entity_id=entity_id, actor_user_id=actor_user_id, before=before, after=after,
                              metadata=metadata)
