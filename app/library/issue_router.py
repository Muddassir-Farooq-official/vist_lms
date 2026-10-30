from __future__ import annotations

import uuid

from pydantic import BaseModel, Field
from fastapi import APIRouter, Body, Depends

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.library.issue_service import BorrowerSearchService, IssueService, enriched_to_detail
from app.library.repository import BookIssueRepository
from app.library.schemas import BookIssueDetail, BorrowerSearchResult, IssueBookRequest, ReturnBookRequest, ReturnPreviewOut

router = APIRouter(prefix="/library", tags=["library"])


@router.get(
    "/borrowers/search",
    response_model=list[BorrowerSearchResult],
    summary="Search students/employees to issue a book to",
)
async def search_borrowers(
    q: str,
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_ISSUE)),
    db=Depends(get_db),
) -> list[BorrowerSearchResult]:
    return await BorrowerSearchService(db).search(q)


@router.post("/issues", response_model=BookIssueDetail, status_code=201, summary="Issue a book to a borrower")
async def issue_book(
    payload: IssueBookRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_ISSUE)),
    db=Depends(get_db),
) -> BookIssueDetail:
    service = IssueService(db)
    issue = await service.issue_book(payload, actor_id=current_user.id)
    enriched = (await service._enrich([issue]))[0]
    return enriched_to_detail(enriched)


@router.get(
    "/issues/{issue_id}/return-preview", response_model=ReturnPreviewOut,
    summary="What returning this book today would cost (days late and the standard late fee)",
)
async def return_preview(
    issue_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_ISSUE)),
    db=Depends(get_db),
) -> ReturnPreviewOut:
    return ReturnPreviewOut(**await IssueService(db).return_preview(issue_id))


@router.post("/issues/{issue_id}/return", response_model=BookIssueDetail, summary="Return a borrowed book")
async def return_book(
    issue_id: uuid.UUID,
    payload: ReturnBookRequest = Body(default_factory=ReturnBookRequest),
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_ISSUE)),
    db=Depends(get_db),
) -> BookIssueDetail:
    service = IssueService(db)
    issue = await service.return_book(issue_id, payload, actor_id=current_user.id, actor_roles=current_user.roles)
    enriched = (await service._enrich([issue]))[0]
    return enriched_to_detail(enriched)


@router.get("/issues/active", response_model=list[BookIssueDetail], summary="List all currently issued books")
async def list_active_issues(
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_VIEW)),
    db=Depends(get_db),
) -> list[BookIssueDetail]:
    service = IssueService(db)
    enriched = await service.list_active_issues_detailed()
    return [enriched_to_detail(e) for e in enriched]


@router.get(
    "/issues/overdue",
    response_model=list[BookIssueDetail],
    summary="List currently overdue loans, oldest due date first — who has what, not just a count",
)
async def list_overdue_issues(
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_VIEW)),
    db=Depends(get_db),
) -> list[BookIssueDetail]:
    service = IssueService(db)
    overdue = await BookIssueRepository(db).list_overdue()
    enriched = await service._enrich(overdue)
    return [enriched_to_detail(e) for e in enriched]


@router.get("/issues/overdue/detailed", summary="Overdue loans with days late, the late fee so far and the borrower's phone")
async def overdue_detailed(current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_VIEW)), db=Depends(get_db)) -> dict:
    from app.core.clock import institute_today
    from app.library.overdue import borrower_contacts

    service = IssueService(db)
    rows = await service._enrich(await BookIssueRepository(db).list_overdue())
    today = institute_today()
    contacts = await borrower_contacts(db, [(e["issue"].borrower_type, e["issue"].borrower_id) for e in rows])
    items = []
    for e in rows:
        d = enriched_to_detail(e).model_dump()
        days, fee = service.late_fee(d["due_date"], today)
        c = contacts.get((d["borrower_type"], d["borrower_id"]), {})
        items.append({**d, "days_overdue": days, "late_fee": fee, "borrower_code": c.get("code"), "borrower_phone": c.get("phone"),
                      "has_login": c.get("user_id") is not None})
    from app.core.config import settings

    return {"items": items, "late_fee_per_day": settings.library_late_fee_per_day, "late_fee_max": settings.library_late_fee_max,
            "total_late_fees": round(sum(i["late_fee"] for i in items), 2), "borrowers": len({(i["borrower_type"], i["borrower_id"]) for i in items})}


class RemindOverdueIn(BaseModel):
    issue_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)    # empty = every overdue loan


@router.post("/issues/overdue/remind", summary="Remind borrowers of overdue books (notification + email); once a day per book")
async def remind_overdue(payload: RemindOverdueIn, current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_ISSUE)),
                         db=Depends(get_db)) -> dict:
    from app.library.overdue import remind

    return await remind(db, payload.issue_ids, actor_id=current_user.id)


@router.post("/issues/{issue_id}/renew", response_model=BookIssueDetail, summary="Staff-assisted renewal of any loan")
async def renew_issue(
    issue_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.LIBRARY_ISSUE)),
    db=Depends(get_db),
) -> BookIssueDetail:
    service = IssueService(db)
    issue = await service.renew(issue_id, actor_id=current_user.id)
    enriched = (await service._enrich([issue]))[0]
    return enriched_to_detail(enriched)