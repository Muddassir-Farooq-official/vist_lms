from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
from app.core.pagination import PageParams, RawPage
from app.core.sequences import next_sequence_value
from app.library.models import Book, BookCopy, BookCopyStatus
from app.library.repository import BookCopyRepository, BookRepository
from app.library.schemas import BookCreateRequest, BookUpdateRequest


class BookService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = BookRepository(session)
        self.copy_repo = BookCopyRepository(session)
        self.audit = AuditService(session)

    async def _generate_short_code(self) -> str:
        """BK-0001, BK-0002, ... — one per book, global sequence.
        Short and typeable, unlike a UUID fragment."""
        seq = await next_sequence_value(self.session, "book_short_code")
        return f"BK-{seq:04d}"

    async def _generate_accession_code(self, book: Book) -> str:
        """<book short_code>-C<seq>, e.g. 'BK-0007-C001', 'BK-0007-C002',
        ... — sequential PER BOOK, so a librarian holding a copy of book
        BK-0007 can immediately tell it's copy #3 of that title without
        cross-referencing anything, which a random 8-char hex suffix
        (the old scheme) couldn't offer. Still fully race-safe under
        concurrent copy creation — same atomic-UPSERT counter used for
        roll numbers/employee IDs, just scoped per book instead of per
        program/year. Existing copies created under the old BC-XXXXXXXX
        scheme are left as-is (see the migration for why); only new
        copies from here on use this format."""
        seq = await next_sequence_value(self.session, f"book_copy:{book.short_code}")
        return f"{book.short_code}-C{seq:03d}"

    async def create_book(self, data: BookCreateRequest, *, actor_id: uuid.UUID) -> tuple[Book, int]:
        short_code = await self._generate_short_code()
        book = Book(
            title=data.title.strip(),
            author=data.author.strip(),
            isbn=data.isbn.strip() if data.isbn else None,
            category=data.category.strip() if data.category else None,
            publisher=data.publisher.strip() if data.publisher else None,
            publication_year=data.publication_year,
            shelf_location=data.shelf_location.strip() if data.shelf_location else None,
            description=data.description.strip() if data.description else None,
            cover_image_url=data.cover_image_url,
            is_journal=data.is_journal,
            journal_volume=data.journal_volume.strip() if data.journal_volume else None,
            journal_issue_number=data.journal_issue_number.strip() if data.journal_issue_number else None,
            short_code=short_code,
        )
        await self.repo.create(book)

        for _ in range(data.initial_copies):
            await self.copy_repo.create(
                BookCopy(book_id=book.id, accession_code=await self._generate_accession_code(book))
            )

        await self.audit.log(
            actor_user_id=actor_id,
            action="library.book_created",
            entity_type="book",
            entity_id=book.id,
            after={"title": book.title, "author": book.author, "initial_copies": data.initial_copies},
        )
        return book, data.initial_copies

    async def get_book(self, book_id: uuid.UUID) -> Book:
        book = await self.repo.get_by_id(book_id)
        if book is None:
            raise ResourceNotFoundError("Book not found.")
        return book

    async def search_books(
        self, *, page: PageParams, query: str | None, is_journal: bool | None = None
    ) -> RawPage[tuple[Book, int, int]]:
        """Returns (book, total_copies, available_copies) tuples so the
        router doesn't need N extra queries to build BookWithCounts."""
        items, total = await self.repo.search(page=page, query=query, is_journal=is_journal)
        enriched = []
        for book in items:
            total_copies, available_copies = await self.repo.copy_counts(book.id)
            enriched.append((book, total_copies, available_copies))
        return RawPage[tuple](items=enriched, total=total, page=page.page, page_size=page.page_size)

    async def update_book(self, book_id: uuid.UUID, data: BookUpdateRequest, *, actor_id: uuid.UUID) -> Book:
        book = await self.get_book(book_id)
        before = {"title": book.title, "author": book.author}

        if data.title is not None:
            book.title = data.title.strip()
        if data.author is not None:
            book.author = data.author.strip()
        if data.isbn is not None:
            book.isbn = data.isbn.strip() or None
        if data.category is not None:
            book.category = data.category.strip() or None
        if data.publisher is not None:
            book.publisher = data.publisher.strip() or None
        if data.publication_year is not None:
            book.publication_year = data.publication_year
        if data.shelf_location is not None:
            book.shelf_location = data.shelf_location.strip() or None
        if data.description is not None:
            book.description = data.description.strip() or None
        if data.cover_image_url is not None:
            book.cover_image_url = data.cover_image_url or None
        if data.is_journal is not None:
            book.is_journal = data.is_journal
        if data.journal_volume is not None:
            book.journal_volume = data.journal_volume.strip() or None
        if data.journal_issue_number is not None:
            book.journal_issue_number = data.journal_issue_number.strip() or None

        await self.audit.log(
            actor_user_id=actor_id,
            action="library.book_updated",
            entity_type="book",
            entity_id=book.id,
            before=before,
            after={"title": book.title, "author": book.author},
        )
        return book

    async def add_copies(self, book_id: uuid.UUID, count: int, *, actor_id: uuid.UUID) -> list[BookCopy]:
        book = await self.get_book(book_id)
        new_copies = []
        for _ in range(count):
            copy = await self.copy_repo.create(
                BookCopy(book_id=book.id, accession_code=await self._generate_accession_code(book))
            )
            new_copies.append(copy)

        await self.audit.log(
            actor_user_id=actor_id,
            action="library.copies_added",
            entity_type="book",
            entity_id=book.id,
            metadata={"count": count},
        )
        return new_copies

    async def list_copies(self, book_id: uuid.UUID) -> list[BookCopy]:
        await self.get_book(book_id)  # 404 if the book itself doesn't exist
        return await self.copy_repo.list_for_book(book_id)

    async def update_copy_status(
        self, copy_id: uuid.UUID, *, status: BookCopyStatus, condition_notes: str | None, actor_id: uuid.UUID
    ) -> BookCopy:
        copy = await self.copy_repo.get_by_id(copy_id)
        if copy is None:
            raise ResourceNotFoundError("Book copy not found.")
        if copy.status == BookCopyStatus.ISSUED and status != BookCopyStatus.ISSUED:
            raise BusinessRuleViolationError(
                "This copy is currently issued — process a return before changing its status."
            )

        old_status = copy.status
        copy.status = status
        if condition_notes is not None:
            copy.condition_notes = condition_notes

        await self.audit.log(
            actor_user_id=actor_id,
            action="library.copy_status_changed",
            entity_type="book_copy",
            entity_id=copy.id,
            before={"status": old_status.value},
            after={"status": status.value},
        )
        return copy