from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.core.pagination import PageParams


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search(
        self,
        *,
        page: PageParams,
        action_prefix: str | None = None,
        action_prefixes: list[str] | None = None,
        entity_type: str | None = None,
        actor_user_id: uuid.UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[list[AuditLog], int]:
        stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
        count_stmt = select(AuditLog)

        conditions = []
        if action_prefix:
            conditions.append(AuditLog.action.like(f"{action_prefix}%"))
        if action_prefixes:
            from sqlalchemy import or_

            conditions.append(or_(*[AuditLog.action.like(f"{p}%") for p in action_prefixes]))
        if entity_type:
            conditions.append(AuditLog.entity_type == entity_type)
        if actor_user_id:
            conditions.append(AuditLog.actor_user_id == actor_user_id)
        if since:
            conditions.append(AuditLog.created_at >= since)
        if until:
            conditions.append(AuditLog.created_at <= until)

        for cond in conditions:
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)

        from sqlalchemy import func

        total = (await self.session.execute(select(func.count()).select_from(count_stmt.subquery()))).scalar_one()

        stmt = stmt.offset((page.page - 1) * page.page_size).limit(page.page_size)
        items = list((await self.session.execute(stmt)).scalars())
        return items, total