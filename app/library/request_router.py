from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.library.issue_service import BorrowerSearchService
from app.library.models import BorrowRequestStatus
from app.library.repository import BookRepository
from app.library.request_schemas import BorrowRequestDecision, BorrowRequestDetail, BorrowRequestOut
from app.library.request_service import BookBorrowRequestService

router = APIRouter(prefix="/library/requests", tags=["library"])


async def _to_detail(db, request) -> BorrowRequestDetail:
    book = await BookRepository(db).get_by_id(request.book_id)
    details = await BorrowerSearchService(db).resolve_details(request.borrower_type, request.borrower_id)
    base = BorrowRequestOut.model_validate(request, from_attributes=True).model_dump()
    return BorrowRequestDetail(
        **base,
        book_title=book.title if book else "Unknown title",
        is_journal=book.is_journal if book else False,
        borrower_name=details.display_name,
        borrower_identifier=details.identifier,
        borrower_program=details.program_name,
        borrower_semester=details.semester,
        borrower_department=details.department,
    )


@router.get("", response_model=list[BorrowRequestDetail], summary="List borrow requests (Librarian/Admin)")
async def list_requests(
    status: BorrowRequestStatus | None = None,
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_ISSUE)),
    db=Depends(get_db),
) -> list[BorrowRequestDetail]:
    requests = await BookBorrowRequestService(db).list_all(status)
    return [await _to_detail(db, r) for r in requests]


@router.post(
    "/{request_id}/approve",
    response_model=BorrowRequestDetail,
    summary="Approve a borrow request — auto-assigns an available copy and issues it",
)
async def approve_request(
    request_id: uuid.UUID,
    payload: BorrowRequestDecision,
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_ISSUE)),
    db=Depends(get_db),
) -> BorrowRequestDetail:
    request = await BookBorrowRequestService(db).approve(
        request_id, actor_id=current_user.id, decision_note=payload.note
    )
    return await _to_detail(db, request)


@router.post("/{request_id}/reject", response_model=BorrowRequestDetail, summary="Reject a borrow request")
async def reject_request(
    request_id: uuid.UUID,
    payload: BorrowRequestDecision,
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_ISSUE)),
    db=Depends(get_db),
) -> BorrowRequestDetail:
    request = await BookBorrowRequestService(db).reject(
        request_id, actor_id=current_user.id, decision_note=payload.note
    )
    return await _to_detail(db, request)