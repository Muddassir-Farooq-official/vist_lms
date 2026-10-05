from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.library.issue_service import IssueService, enriched_to_detail
from app.library.models import Book, BookCopy, BookCopyStatus
from app.library.repository import BookIssueRepository
from app.library.request_repository import BookBorrowRequestRepository
from app.library.schemas import LibraryDashboardResponse


class LibraryDashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.issue_repo = BookIssueRepository(session)
        self.issue_service = IssueService(session)
        self.request_repo = BookBorrowRequestRepository(session)

    async def get_dashboard(self) -> LibraryDashboardResponse:
        total_titles = (await self.session.execute(select(func.count()).select_from(Book))).scalar_one()
        total_copies = (await self.session.execute(select(func.count()).select_from(BookCopy))).scalar_one()
        available_copies = (
            await self.session.execute(
                select(func.count()).select_from(BookCopy).where(BookCopy.status == BookCopyStatus.AVAILABLE)
            )
        ).scalar_one()

        issued_count, overdue_count = await self.issue_repo.counts()
        pending_requests_count = await self.request_repo.count_pending()

        recent_issues_raw = await self.issue_repo.recent_issues()
        recent_returns_raw = await self.issue_repo.recent_returns()

        recent_issues = [enriched_to_detail(e) for e in await self.issue_service._enrich(recent_issues_raw)]
        recent_returns = [enriched_to_detail(e) for e in await self.issue_service._enrich(recent_returns_raw)]

        return LibraryDashboardResponse(
            total_titles=total_titles,
            total_copies=total_copies,
            available_copies=available_copies,
            issued_copies=issued_count,
            overdue_count=overdue_count,
            pending_requests_count=pending_requests_count,
            recent_issues=recent_issues,
            recent_returns=recent_returns,
        )