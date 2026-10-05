from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fines.models import Fine, FinePayerType, FineStatus


class FineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, fine_id: uuid.UUID) -> Fine | None:
        return await self.session.get(Fine, fine_id)

    async def create(self, fine: Fine) -> Fine:
        self.session.add(fine)
        await self.session.flush()
        return fine

    async def list_all(
        self,
        *,
        payer_type: FinePayerType | None = None,
        payer_id: uuid.UUID | None = None,
        status: FineStatus | None = None,
        source: str | None = None,
    ) -> list[Fine]:
        stmt = select(Fine).order_by(Fine.issued_at.desc())
        if payer_type is not None:
            stmt = stmt.where(Fine.payer_type == payer_type)
        if payer_id is not None:
            stmt = stmt.where(Fine.payer_id == payer_id)
        if status is not None:
            stmt = stmt.where(Fine.status == status)
        if source is not None:
            stmt = stmt.where(Fine.source == source)
        return list((await self.session.execute(stmt)).scalars())

    async def list_visible_to_payer(self, payer_type: FinePayerType, payer_id: uuid.UUID) -> list[Fine]:
        """What a student/employee is allowed to see about their OWN
        fines — deliberately excludes PENDING_APPROVAL (a request that
        hasn't been reviewed yet) and REJECTED (a request an admin
        denied) — the payer never knew about either and must not find
        out via this endpoint. Only a fine that was actually approved
        (PENDING, PAID, or WAIVED — every status that ever WAS or still
        IS a real charge) is visible here."""
        stmt = (
            select(Fine)
            .where(
                Fine.payer_type == payer_type,
                Fine.payer_id == payer_id,
                Fine.status.in_([FineStatus.PENDING, FineStatus.PAID, FineStatus.WAIVED]),
            )
            .order_by(Fine.issued_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars())

    async def list_requested_by(self, user_id: uuid.UUID) -> list[Fine]:
        """Lets a librarian/accounts officer track what happened to the
        fines THEY requested — approved, rejected, or still pending —
        since they can't see it any other way (GET /fines and /fines/me
        both answer a different question: 'fines against a payer', not
        'requests I made')."""
        stmt = select(Fine).where(Fine.issued_by == user_id).order_by(Fine.issued_at.desc())
        return list((await self.session.execute(stmt)).scalars())

    async def balance_for(self, payer_type: FinePayerType, payer_id: uuid.UUID) -> dict:
        stmt = select(Fine.status, func.coalesce(func.sum(Fine.amount), 0), func.count()).where(
            Fine.payer_type == payer_type, Fine.payer_id == payer_id
        ).group_by(Fine.status)
        rows = (await self.session.execute(stmt)).all()

        totals = {"total_pending": 0.0, "total_paid": 0.0, "total_waived": 0.0, "pending_count": 0}
        for status, total, count in rows:
            if status == FineStatus.PENDING:
                totals["total_pending"] = float(total)
                totals["pending_count"] = count
            elif status == FineStatus.PAID:
                totals["total_paid"] = float(total)
            elif status == FineStatus.WAIVED:
                totals["total_waived"] = float(total)
        return totals