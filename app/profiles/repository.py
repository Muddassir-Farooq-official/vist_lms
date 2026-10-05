from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.profiles.models import ProfilePartyType, Qualification


class QualificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, qualification_id: uuid.UUID) -> Qualification | None:
        return await self.session.get(Qualification, qualification_id)

    async def list_for_party(self, party_type: ProfilePartyType, party_id: uuid.UUID) -> list[Qualification]:
        stmt = (
            select(Qualification)
            .where(Qualification.party_type == party_type, Qualification.party_id == party_id)
            .order_by(Qualification.display_order, Qualification.year_completed)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def create(self, qualification: Qualification) -> Qualification:
        self.session.add(qualification)
        await self.session.flush()
        return qualification

    async def delete(self, qualification: Qualification) -> None:
        await self.session.delete(qualification)