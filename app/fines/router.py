from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from app.auth.permissions import Permissions
from app.auth.repository import AuthRepository
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.core.exceptions import AuthorizationError
from app.core.pagination import PageParams
from app.employees.repository import EmployeeRepository
from app.fines.models import FinePayerType, FineSource, FineStatus
from app.fines.repository import FineRepository
from app.fines.schemas import (
    FineBalanceOut,
    FineCreateRequest,
    FineDetail,
    FineOut,
    FinePayerSearchResult,
    FineRejectRequest,
    FineRequestCreate,
    FineResolveRequest,
    FinePaidRequest,
)
from app.fines.service import FineService, default_source_for_roles, resolve_payer
from app.students.repository import StudentRepository

router = APIRouter(prefix="/fines", tags=["fines"])


async def _resolve_own_payer(db, current_user: CurrentUser) -> tuple[FinePayerType, uuid.UUID] | None:
    student = await StudentRepository(db).get_by_user_id(current_user.id)
    if student is not None:
        return FinePayerType.STUDENT, student.id
    employee = await EmployeeRepository(db).get_by_user_id(current_user.id)
    if employee is not None:
        return FinePayerType.EMPLOYEE, employee.id
    return None


async def _to_detail(db, fine) -> FineDetail:
    payer = await resolve_payer(db, fine.payer_type, fine.payer_id)
    issued_by_name = None
    if fine.issued_by is not None:
        user = await AuthRepository(db).get_user_by_id(fine.issued_by)
        issued_by_name = user.username if user else None
    approved_by_name = None
    if fine.approved_by is not None:
        user = await AuthRepository(db).get_user_by_id(fine.approved_by)
        approved_by_name = user.username if user else None
    resolved_by_name = None
    if fine.resolved_by is not None:
        user = await AuthRepository(db).get_user_by_id(fine.resolved_by)
        resolved_by_name = user.username if user else None

    base = FineOut.model_validate(fine).model_dump()
    return FineDetail(
        **base,
        payer_name=payer.name,
        payer_identifier=payer.identifier,
        issued_by_name=issued_by_name,
        approved_by_name=approved_by_name,
        resolved_by_name=resolved_by_name,
    )


@router.get(
    "/search-payers",
    response_model=list[FinePayerSearchResult],
    summary="Search students/employees to pick a fine target — self-contained, doesn't need STUDENT_VIEW/EMPLOYEE_VIEW",
)
async def search_payers(
    payer_type: FinePayerType,
    q: str = Query(min_length=1, max_length=100),
    current_user: CurrentUser = Depends(require_permission(Permissions.FINE_ISSUE)),
    db=Depends(get_db),
) -> list[FinePayerSearchResult]:
    """Deliberately its own minimal lookup rather than reusing
    GET /students or GET /employees — those require STUDENT_VIEW /
    EMPLOYEE_VIEW respectively, which LIBRARIAN/TEACHER/ACCOUNTS_OFFICER
    don't all hold, and shouldn't need to just to pick who a fine is
    against. Returns name + identifier only, nothing else."""
    page = PageParams(page=1, page_size=10)
    if payer_type == FinePayerType.STUDENT:
        students, _ = await StudentRepository(db).search(page=page, query=q)
        return [
            FinePayerSearchResult(id=s.id, name=f"{s.first_name} {s.last_name}", identifier=s.roll_number)
            for s in students
        ]

    employees, _ = await EmployeeRepository(db).search(page=page, query=q, status=None, job_role_id=None)
    return [
        FinePayerSearchResult(id=e.id, name=f"{e.first_name} {e.last_name}", identifier=e.employee_code)
        for e in employees
    ]


@router.post("", response_model=FineDetail, status_code=201, summary="Issue a fine directly, already approved (admin only)")
async def issue_fine(
    payload: FineCreateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.FINE_ISSUE)),
    db=Depends(get_db),
) -> FineDetail:
    fine = await FineService(db).issue(
        payer_type=payload.payer_type,
        payer_id=payload.payer_id,
        amount=payload.amount,
        reason=payload.reason,
        source=payload.source,
        actor_id=current_user.id,
    )
    return await _to_detail(db, fine)


@router.post(
    "/request", response_model=FineDetail, status_code=201,
    summary="Request a fine for admin approval (librarian/accounts officer) — not yet chargeable or visible to the payer",
)
async def request_fine(
    payload: FineRequestCreate,
    current_user: CurrentUser = Depends(require_permission(Permissions.FINE_REQUEST)),
    db=Depends(get_db),
) -> FineDetail:
    source = default_source_for_roles(current_user.roles)
    fine = await FineService(db).request(
        payer_type=payload.payer_type,
        payer_id=payload.payer_id,
        amount=payload.amount,
        reason=payload.reason,
        source=source,
        actor_id=current_user.id,
        actor_roles=current_user.roles,
    )
    return await _to_detail(db, fine)


@router.get(
    "/my-requests", response_model=list[FineDetail],
    summary="Fines I've requested and their status (librarian/accounts officer)",
)
async def my_requests(
    current_user: CurrentUser = Depends(require_permission(Permissions.FINE_REQUEST)),
    db=Depends(get_db),
) -> list[FineDetail]:
    fines = await FineRepository(db).list_requested_by(current_user.id)
    return [await _to_detail(db, f) for f in fines]


@router.post("/{fine_id}/approve", response_model=FineDetail, summary="Approve a requested fine — makes it real and visible to the payer")
async def approve_fine(
    fine_id: uuid.UUID,
    payload: FineResolveRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.FINE_ISSUE)),
    db=Depends(get_db),
) -> FineDetail:
    fine = await FineService(db).approve(fine_id, actor_id=current_user.id, note=payload.note)
    return await _to_detail(db, fine)


@router.post("/{fine_id}/reject", response_model=FineDetail, summary="Reject a requested fine — the requester is told why, the payer never finds out")
async def reject_fine(
    fine_id: uuid.UUID,
    payload: FineRejectRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.FINE_ISSUE)),
    db=Depends(get_db),
) -> FineDetail:
    fine = await FineService(db).reject(fine_id, actor_id=current_user.id, note=payload.note)
    return await _to_detail(db, fine)


@router.get("", response_model=list[FineDetail], summary="List fines (scoped to own record unless you hold fine.manage)")
async def list_fines(
    payer_type: FinePayerType | None = None,
    payer_id: uuid.UUID | None = None,
    status: FineStatus | None = None,
    source: FineSource | None = None,
    current_user: CurrentUser = Depends(require_permission(Permissions.FINE_VIEW)),
    db=Depends(get_db),
) -> list[FineDetail]:
    if Permissions.FINE_MANAGE not in current_user.permissions:
        # Scoped: STUDENT/TEACHER holding only fine.view can only ever
        # see their own fines, regardless of what filters they pass —
        # and only the ones that were actually approved (same
        # visibility rule as /me below): a status filter can't be used
        # to peek at a PENDING_APPROVAL or REJECTED request against
        # themselves that they were never meant to see.
        own = await _resolve_own_payer(db, current_user)
        if own is None:
            raise AuthorizationError("This account is not linked to a student or employee record.")
        payer_type, payer_id = own
        fines = await FineRepository(db).list_visible_to_payer(payer_type, payer_id)
        if status is not None:
            fines = [f for f in fines if f.status == status]
        if source is not None:
            fines = [f for f in fines if f.source == source]
        return [await _to_detail(db, f) for f in fines]

    fines = await FineRepository(db).list_all(payer_type=payer_type, payer_id=payer_id, status=status, source=source)
    return [await _to_detail(db, f) for f in fines]


@router.get("/late-fees-building", summary="Late fees growing on fee instalments that are still unpaid (not fines yet)")
async def late_fees_building(
    current_user: CurrentUser = Depends(require_permission(Permissions.PAYMENT_RECORD)),       # administration and accounts
    db=Depends(get_db),
) -> dict:
    from app.fees.late_fees import building

    return await building(db)


@router.post("/late-fees-building/post-all", summary="Post every late fee built up so far as fines now")
async def post_all_late_fees(
    current_user: CurrentUser = Depends(require_permission(Permissions.FINE_ISSUE)),
    db=Depends(get_db),
) -> dict:
    from app.fees.late_fees import post_all

    return await post_all(db, current_user.id)


@router.get("/me", response_model=list[FineDetail], summary="My own fines")
async def my_fines(
    current_user: CurrentUser = Depends(require_permission(Permissions.FINE_VIEW)),
    db=Depends(get_db),
) -> list[FineDetail]:
    own = await _resolve_own_payer(db, current_user)
    if own is None:
        raise AuthorizationError("This account is not linked to a student or employee record.")
    payer_type, payer_id = own
    fines = await FineRepository(db).list_visible_to_payer(payer_type, payer_id)
    return [await _to_detail(db, f) for f in fines]


@router.get("/me/balance", response_model=FineBalanceOut, summary="My own fine balance")
async def my_balance(
    current_user: CurrentUser = Depends(require_permission(Permissions.FINE_VIEW)),
    db=Depends(get_db),
) -> FineBalanceOut:
    own = await _resolve_own_payer(db, current_user)
    if own is None:
        raise AuthorizationError("This account is not linked to a student or employee record.")
    payer_type, payer_id = own
    totals = await FineRepository(db).balance_for(payer_type, payer_id)
    return FineBalanceOut(payer_type=payer_type, payer_id=payer_id, **totals)


@router.get(
    "/balance/{payer_type}/{payer_id}",
    response_model=FineBalanceOut,
    summary="Fine balance for any student/employee (staff — for deep tracking)",
)
async def balance_for(
    payer_type: FinePayerType,
    payer_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.FINE_VIEW)),
    db=Depends(get_db),
) -> FineBalanceOut:
    if Permissions.FINE_MANAGE not in current_user.permissions:
        own = await _resolve_own_payer(db, current_user)
        if own is None or own != (payer_type, payer_id):
            raise AuthorizationError("You can only view your own fine balance.")
    totals = await FineRepository(db).balance_for(payer_type, payer_id)
    return FineBalanceOut(payer_type=payer_type, payer_id=payer_id, **totals)


@router.post("/{fine_id}/mark-paid", response_model=FineDetail, summary="Mark a fine as paid")
async def mark_fine_paid(
    fine_id: uuid.UUID,
    payload: FinePaidRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.FINE_MANAGE)),
    db=Depends(get_db),
) -> FineDetail:
    fine = await FineService(db).mark_paid(fine_id, actor_id=current_user.id, note=payload.note, method=payload.method,
                                           reference=payload.reference, paid_on=payload.paid_on)
    return await _to_detail(db, fine)


@router.post("/{fine_id}/waive", response_model=FineDetail, summary="Waive a fine (forgiven, not collected)")
async def waive_fine(
    fine_id: uuid.UUID,
    payload: FineResolveRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.FINE_WAIVE)),
    db=Depends(get_db),
) -> FineDetail:
    """Waiving is an ADMIN-only administrative decision — deliberately a
    separate permission from FINE_MANAGE (mark-paid), which Accounts
    Officer also holds."""
    from fastapi.responses import JSONResponse

    from app.approvals.service import gate, pending_response

    service = FineService(db)
    fine = await service.get(fine_id)
    if fine.status.value == "PENDING" and len((payload.note or "").strip()) >= 4:
        # A second administrator approves a waiver (owner decision 10); with one administrator it goes ahead now.
        req = await gate(db, kind="FINE_WAIVE", actor_id=current_user.id, target_id=fine.id, amount=float(fine.amount), reason=payload.note,
                         payload={"fine_id": fine.id, "note": payload.note}, summary=f"Waive fine of Rs. {float(fine.amount):,.0f}: {fine.reason}"[:300])
        if req is not None:
            return JSONResponse(status_code=202, content=pending_response(req))
    fine = await service.waive(fine_id, actor_id=current_user.id, note=payload.note)
    return await _to_detail(db, fine)

@router.get("/{fine_id}/receipt.pdf", summary="Receipt for a paid fine (the accounts office, or the person who paid it)")
async def fine_receipt_pdf(
    fine_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.FINE_VIEW)),
    db=Depends(get_db),
):
    from fastapi.responses import Response

    from app.core.config import settings
    from app.core.exceptions import ResourceNotFoundError
    from app.fees.receipt_pdf import render_receipt_pdf
    from app.fines.service import _resolve_user_id

    fine = await FineService(db).get(fine_id)
    if fine.status != FineStatus.PAID or not fine.receipt_number:
        raise ResourceNotFoundError("Receipt not found.")
    if Permissions.FINE_MANAGE not in current_user.permissions:
        if await _resolve_user_id(db, fine.payer_type, fine.payer_id) != current_user.id:
            raise ResourceNotFoundError("Receipt not found.")
    payer = await resolve_payer(db, fine.payer_type, fine.payer_id)
    data = {"institute": settings.app_name, "receipt_number": fine.receipt_number, "paid_on": fine.paid_on,
            "recorded_on": fine.resolved_at.date() if fine.resolved_at else fine.paid_on,
            "payer": {"name": payer.name, "identifier": payer.identifier, "group": fine.payer_type.value.title()},
            "fee_name": f"Fine — {fine.source.value.replace('_', ' ').title()}", "term_label": None,
            "amount": float(fine.amount), "method": fine.payment_method, "reference": fine.payment_reference,
            "description": fine.reason}
    return Response(content=await run_in_threadpool(render_receipt_pdf, data), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="receipt-{fine.receipt_number}.pdf"'})