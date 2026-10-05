from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.library.models import BookBorrowRequest, BorrowerType, BorrowRequestStatus


class BookBorrowRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, request_id: uuid.UUID) -> BookBorrowRequest | None:
        return await self.session.get(BookBorrowRequest, request_id)

    async def list_for_borrower(
        self, borrower_type: BorrowerType, borrower_id: uuid.UUID
    ) -> list[BookBorrowRequest]:
        stmt = (
            select(BookBorrowRequest)
            .where(BookBorrowRequest.borrower_type == borrower_type, BookBorrowRequest.borrower_id == borrower_id)
            .order_by(BookBorrowRequest.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars())

    async def list_by_status(self, status: BorrowRequestStatus | None) -> list[BookBorrowRequest]:
        stmt = select(BookBorrowRequest).order_by(BookBorrowRequest.created_at.desc())
        if status is not None:
            stmt = stmt.where(BookBorrowRequest.status == status)
        return list((await self.session.execute(stmt)).scalars())

    async def count_pending(self) -> int:
        stmt = select(func.count()).select_from(BookBorrowRequest).where(
            BookBorrowRequest.status == BorrowRequestStatus.PENDING
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def count_pending_for_borrower_and_book(
        self, borrower_type: BorrowerType, borrower_id: uuid.UUID, book_id: uuid.UUID
    ) -> int:
        """PENDING or WAITLISTED both count as 'you already have an open
        ask for this title' — no reason to let someone queue twice."""
        stmt = select(BookBorrowRequest).where(
            BookBorrowRequest.borrower_type == borrower_type,
            BookBorrowRequest.borrower_id == borrower_id,
            BookBorrowRequest.book_id == book_id,
            BookBorrowRequest.status.in_([BorrowRequestStatus.PENDING, BorrowRequestStatus.WAITLISTED]),
        )
        return len(list((await self.session.execute(stmt)).scalars()))

    async def create(self, request: BookBorrowRequest) -> BookBorrowRequest:
        self.session.add(request)
        await self.session.flush()
        return request