from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.complaints.models import Complaint, ComplaintPartyType, ComplaintStatus

OPEN_STATUSES = (ComplaintStatus.SUBMITTED, ComplaintStatus.UNDER_REVIEW)


class ComplaintRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, complaint_id: uuid.UUID) -> Complaint | None:
        return await self.session.get(Complaint, complaint_id)

    async def create(self, complaint: Complaint) -> Complaint:
        self.session.add(complaint)
        await self.session.flush()
        return complaint

    async def list_all(
        self,
        *,
        status: ComplaintStatus | None = None,
        target_type: ComplaintPartyType | None = None,
        target_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> list[Complaint]:
        stmt = select(Complaint).order_by(Complaint.created_at.desc())
        if status is not None:
            stmt = stmt.where(Complaint.status == status)
        if target_type is not None:
            stmt = stmt.where(Complaint.target_type == target_type)
        if target_id is not None:
            stmt = stmt.where(Complaint.target_id == target_id)
        if reason is not None:
            stmt = stmt.where(Complaint.reason == reason)
        return list((await self.session.execute(stmt)).scalars())

    async def list_for_complainant(
        self, complainant_type: ComplaintPartyType, complainant_id: uuid.UUID
    ) -> list[Complaint]:
        stmt = (
            select(Complaint)
            .where(Complaint.complainant_type == complainant_type, Complaint.complainant_id == complainant_id)
            .order_by(Complaint.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars())

    async def target_counts(self) -> list[tuple[ComplaintPartyType, uuid.UUID, int, int]]:
        """One row per target: (target_type, target_id, total_count,
        open_count) — the raw aggregate the analytics endpoint builds
        its per-person breakdown from."""
        stmt = (
            select(
                Complaint.target_type,
                Complaint.target_id,
                func.count().label("total_count"),
            )
            .group_by(Complaint.target_type, Complaint.target_id)
            .order_by(func.count().desc())
        )
        rows = (await self.session.execute(stmt)).all()

        results = []
        for target_type, target_id, total_count in rows:
            open_stmt = select(func.count()).select_from(Complaint).where(
                Complaint.target_type == target_type,
                Complaint.target_id == target_id,
                Complaint.status.in_(OPEN_STATUSES),
            )
            open_count = (await self.session.execute(open_stmt)).scalar_one()
            results.append((target_type, target_id, total_count, open_count))
        return results