from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.core.pagination import Page, page_params, PageParams
from app.library.schemas import (
    AddCopiesRequest,
    BookCopyOut,
    BookCreateRequest,
    BookOut,
    BookUpdateRequest,
    BookWithCounts,
    CopyStatusUpdateRequest,
)
from app.library.service import BookService

router = APIRouter(prefix="/library/books", tags=["library"])


def _with_counts(book, total: int, available: int) -> BookWithCounts:
    return BookWithCounts(**BookOut.model_validate(book).model_dump(), total_copies=total, available_copies=available)


@router.get("", response_model=Page[BookWithCounts], summary="Search/list the book catalogue")
async def list_books(
    q: str | None = None,
    is_journal: bool | None = None,
    page: PageParams = Depends(page_params),
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_VIEW)),
    db=Depends(get_db),
) -> Page[BookWithCounts]:
    """`is_journal=true` returns only the Journals section (e.g. Nursing
    Journals); omit it to get the general book catalogue plus journals
    together, or `is_journal=false` for books only."""
    result = await BookService(db).search_books(page=page, query=q, is_journal=is_journal)
    items = [_with_counts(book, total, available) for book, total, available in result.items]
    return Page[BookWithCounts](items=items, total=result.total, page=result.page, page_size=result.page_size)


@router.post("", response_model=BookWithCounts, status_code=201, summary="Add a book to the catalogue")
async def create_book(
    payload: BookCreateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_MANAGE)),
    db=Depends(get_db),
) -> BookWithCounts:
    book, copy_count = await BookService(db).create_book(payload, actor_id=current_user.id)
    return _with_counts(book, copy_count, copy_count)


@router.get("/{book_id}", response_model=BookWithCounts, summary="Get book detail with copy counts")
async def get_book(
    book_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_VIEW)),
    db=Depends(get_db),
) -> BookWithCounts:
    service = BookService(db)
    book = await service.get_book(book_id)
    total, available = await service.repo.copy_counts(book_id)
    return _with_counts(book, total, available)


@router.patch("/{book_id}", response_model=BookWithCounts, summary="Edit book details")
async def update_book(
    book_id: uuid.UUID,
    payload: BookUpdateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_MANAGE)),
    db=Depends(get_db),
) -> BookWithCounts:
    service = BookService(db)
    book = await service.update_book(book_id, payload, actor_id=current_user.id)
    total, available = await service.repo.copy_counts(book_id)
    return _with_counts(book, total, available)


@router.get("/{book_id}/copies", response_model=list[BookCopyOut], summary="List all copies of a book")
async def list_copies(
    book_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_VIEW)),
    db=Depends(get_db),
) -> list[BookCopyOut]:
    copies = await BookService(db).list_copies(book_id)
    return [BookCopyOut.model_validate(c) for c in copies]


@router.post(
    "/{book_id}/copies", response_model=list[BookCopyOut], status_code=201, summary="Add more copies of a book"
)
async def add_copies(
    book_id: uuid.UUID,
    payload: AddCopiesRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_MANAGE)),
    db=Depends(get_db),
) -> list[BookCopyOut]:
    copies = await BookService(db).add_copies(book_id, payload.count, actor_id=current_user.id)
    return [BookCopyOut.model_validate(c) for c in copies]


@router.patch(
    "/copies/{copy_id}", response_model=BookCopyOut, summary="Change a copy's status (lost/damaged/retired)"
)
async def update_copy_status(
    copy_id: uuid.UUID,
    payload: CopyStatusUpdateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_MANAGE)),
    db=Depends(get_db),
) -> BookCopyOut:
    copy = await BookService(db).update_copy_status(
        copy_id, status=payload.status, condition_notes=payload.condition_notes, actor_id=current_user.id
    )
    return BookCopyOut.model_validate(copy)