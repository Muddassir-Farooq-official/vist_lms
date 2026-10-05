from __future__ import annotations

from app.core.clock import institute_today

import uuid
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import PageParams
from app.library.models import Book, BookCopy, BookCopyStatus, BookIssue, IssueStatus


class BookRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, book_id: uuid.UUID) -> Book | None:
        return await self.session.get(Book, book_id)

    async def search(
        self, *, page: PageParams, query: str | None, is_journal: bool | None = None
    ) -> tuple[list[Book], int]:
        stmt = select(Book)
        count_stmt = select(func.count()).select_from(Book)

        if query:
            like = f"%{query}%"
            cond = or_(Book.title.ilike(like), Book.author.ilike(like), Book.isbn == query)
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)

        if is_journal is not None:
            stmt = stmt.where(Book.is_journal.is_(is_journal))
            count_stmt = count_stmt.where(Book.is_journal.is_(is_journal))

        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(Book.title).offset(page.offset).limit(page.page_size)
        result = await self.session.execute(stmt)
        return list(result.scalars()), total

    async def create(self, book: Book) -> Book:
        self.session.add(book)
        await self.session.flush()
        return book

    async def copy_counts(self, book_id: uuid.UUID) -> tuple[int, int]:
        total_stmt = select(func.count()).select_from(BookCopy).where(BookCopy.book_id == book_id)
        available_stmt = (
            select(func.count())
            .select_from(BookCopy)
            .where(BookCopy.book_id == book_id, BookCopy.status == BookCopyStatus.AVAILABLE)
        )
        total = (await self.session.execute(total_stmt)).scalar_one()
        available = (await self.session.execute(available_stmt)).scalar_one()
        return total, available


class BookCopyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, copy_id: uuid.UUID) -> BookCopy | None:
        return await self.session.get(BookCopy, copy_id)

    async def list_for_book(self, book_id: uuid.UUID) -> list[BookCopy]:
        stmt = select(BookCopy).where(BookCopy.book_id == book_id).order_by(BookCopy.accession_code)
        return list((await self.session.execute(stmt)).scalars())

    async def count_for_book(self, book_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(BookCopy).where(BookCopy.book_id == book_id)
        return (await self.session.execute(stmt)).scalar_one()

    async def create(self, copy: BookCopy) -> BookCopy:
        self.session.add(copy)
        await self.session.flush()
        return copy

    async def get_by_accession_code(self, code: str) -> BookCopy | None:
        stmt = select(BookCopy).where(BookCopy.accession_code == code)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_first_available_for_book(self, book_id: uuid.UUID) -> BookCopy | None:
        """Used when approving a borrow request — the librarian approves
        the TITLE, not a specific copy, so we hand out whichever
        AVAILABLE copy comes first (by accession code, for determinism)."""
        stmt = (
            select(BookCopy)
            .where(BookCopy.book_id == book_id, BookCopy.status == BookCopyStatus.AVAILABLE)
            .order_by(BookCopy.accession_code)
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class BookIssueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, issue_id: uuid.UUID) -> BookIssue | None:
        return await self.session.get(BookIssue, issue_id)

    async def get_active_issue_for_copy(self, copy_id: uuid.UUID) -> BookIssue | None:
        stmt = select(BookIssue).where(BookIssue.book_copy_id == copy_id, BookIssue.status == IssueStatus.ISSUED)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_active_for_borrower(self, borrower_type: str, borrower_id: uuid.UUID) -> list[BookIssue]:
        stmt = select(BookIssue).where(
            BookIssue.borrower_type == borrower_type,
            BookIssue.borrower_id == borrower_id,
            BookIssue.status == IssueStatus.ISSUED,
        )
        return list((await self.session.execute(stmt)).scalars())

    async def list_history_for_borrower(self, borrower_type: str, borrower_id: uuid.UUID) -> list[BookIssue]:
        """Past (returned/lost) issues, most recent first — used by the
        student/faculty portal's 'my loans' history tab."""
        stmt = (
            select(BookIssue)
            .where(
                BookIssue.borrower_type == borrower_type,
                BookIssue.borrower_id == borrower_id,
                BookIssue.status != IssueStatus.ISSUED,
            )
            .order_by(BookIssue.issued_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars())

    async def create(self, issue: BookIssue) -> BookIssue:
        self.session.add(issue)
        await self.session.flush()
        return issue

    async def counts(self) -> tuple[int, int]:
        issued_stmt = select(func.count()).select_from(BookIssue).where(BookIssue.status == IssueStatus.ISSUED)
        overdue_stmt = (
            select(func.count())
            .select_from(BookIssue)
            .where(BookIssue.status == IssueStatus.ISSUED, BookIssue.due_date < institute_today())
        )
        issued = (await self.session.execute(issued_stmt)).scalar_one()
        overdue = (await self.session.execute(overdue_stmt)).scalar_one()
        return issued, overdue

    async def recent_issues(self, limit: int = 5) -> list[BookIssue]:
        stmt = select(BookIssue).order_by(BookIssue.created_at.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars())

    async def list_overdue(self) -> list[BookIssue]:
        """All currently-overdue loans, oldest due date first — the
        actual actionable list a librarian needs, not just the bare
        count already shown on the dashboard."""
        stmt = (
            select(BookIssue)
            .where(BookIssue.status == IssueStatus.ISSUED, BookIssue.due_date < institute_today())
            .order_by(BookIssue.due_date.asc())
        )
        return list((await self.session.execute(stmt)).scalars())

    async def recent_returns(self, limit: int = 5) -> list[BookIssue]:
        stmt = (
            select(BookIssue)
            .where(BookIssue.status == IssueStatus.RETURNED)
            .order_by(BookIssue.returned_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def list_all_active(self) -> list[BookIssue]:
        stmt = select(BookIssue).where(BookIssue.status == IssueStatus.ISSUED)
        return list((await self.session.execute(stmt)).scalars())