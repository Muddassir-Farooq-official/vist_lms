from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.auth.permissions import Permissions, Roles
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_any_role, require_permission
from app.core.exceptions import AuthorizationError, ResourceNotFoundError
from app.employees.repository import EmployeeRepository
from app.fees.assignment_service import ChallanService, FeeAssignmentService
from app.fees.models import FeeStructure, PayerType
from app.fees.repository import FeeAssignmentRepository
from app.fees.schemas import (
    AssignmentStatusUpdateRequest,
    ChallanOut,
    FeeAssignmentOut,
    LedgerEntryOut,
    RecordPaymentRequest,
)
from app.students.repository import StudentRepository

router = APIRouter(prefix="/fees/assignments", tags=["fees"])

# NOTE — security fix: these three list/detail endpoints used to be gated
# by the generic FEE_VIEW permission, which STUDENT (correctly) also
# holds for viewing their OWN fee record. Because these endpoints never
# filtered by ownership, that meant ANY logged-in student could call them
# and see every other student's and staff member's exact charges,
# payments, and balances — confirmed live before this fix, not
# theoretical. Now explicitly restricted to ADMIN/ACCOUNTS_OFFICER, the
# only roles that should ever see the whole institution's financial data.
# Students/employees get their own data through the new /me endpoint
# below instead, which filters by their own identity server-side.
_STAFF_ONLY = require_any_role(Roles.ADMIN, Roles.ACCOUNTS_OFFICER)


async def _to_assignment_out(db, assignment) -> FeeAssignmentOut:
    service = FeeAssignmentService(db)
    structure = await db.get(FeeStructure, assignment.fee_structure_id)
    payer_name = await service.resolve_payer_name(assignment.payer_type, assignment.payer_id)
    status_data = await service.get_status(assignment)

    return FeeAssignmentOut(
        id=assignment.id,
        fee_structure_id=assignment.fee_structure_id,
        fee_structure_name=structure.name if structure else "Unknown",
        payer_type=assignment.payer_type,
        payer_id=assignment.payer_id,
        payer_name=payer_name,
        status=assignment.status,
        is_in_good_standing=status_data["total_due_now"] <= 0 and assignment.status.value == "ACTIVE",
        **status_data,
    )


async def _resolve_own_payer(db, current_user: CurrentUser) -> tuple[PayerType, uuid.UUID] | None:
    """Maps a logged-in user to their own Student or Employee record, if
    any — this is what makes the /me endpoints below actually scoped to
    'your own data' rather than trusting a client-supplied ID."""
    student = await StudentRepository(db).get_by_user_id(current_user.id)
    if student is not None:
        return PayerType.STUDENT, student.id
    employee = await EmployeeRepository(db).get_by_user_id(current_user.id)
    if employee is not None:
        return PayerType.EMPLOYEE, employee.id
    return None


@router.get("", response_model=list[FeeAssignmentOut], summary="List all fee assignments (Admin/Accounts Officer only)")
async def list_assignments(
    current_user: CurrentUser = Depends(_STAFF_ONLY),
    db=Depends(get_db),
) -> list[FeeAssignmentOut]:
    service = FeeAssignmentService(db)
    assignments = await service.list_all()
    return [await _to_assignment_out(db, a) for a in assignments]


@router.get("/me", response_model=list[FeeAssignmentOut], summary="List the current user's own fee assignments")
async def list_my_assignments(
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_VIEW)),
    db=Depends(get_db),
) -> list[FeeAssignmentOut]:
    own = await _resolve_own_payer(db, current_user)
    if own is None:
        return []
    payer_type, payer_id = own
    service = FeeAssignmentService(db)
    assignments = await service.list_for_payer(payer_type, payer_id)
    return [await _to_assignment_out(db, a) for a in assignments]


@router.get("/{assignment_id}", response_model=FeeAssignmentOut, summary="Get a fee assignment (Admin/Accounts Officer only)")
async def get_assignment(
    assignment_id: uuid.UUID,
    current_user: CurrentUser = Depends(_STAFF_ONLY),
    db=Depends(get_db),
) -> FeeAssignmentOut:
    service = FeeAssignmentService(db)
    assignment = await service.get(assignment_id)
    return await _to_assignment_out(db, assignment)


@router.get(
    "/{assignment_id}/ledger",
    response_model=list[LedgerEntryOut],
    summary="Get the ledger for an assignment (Admin/Accounts Officer only)",
)
async def get_ledger(
    assignment_id: uuid.UUID,
    current_user: CurrentUser = Depends(_STAFF_ONLY),
    db=Depends(get_db),
) -> list[LedgerEntryOut]:
    entries = await FeeAssignmentRepository(db).get_ledger(assignment_id)
    return [LedgerEntryOut.model_validate(e) for e in entries]


@router.get(
    "/me/{assignment_id}/ledger",
    response_model=list[LedgerEntryOut],
    summary="Get the ledger for one of the current user's OWN assignments",
)
async def get_my_ledger(
    assignment_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_VIEW)),
    db=Depends(get_db),
) -> list[LedgerEntryOut]:
    service = FeeAssignmentService(db)
    assignment = await service.get(assignment_id)  # 404s cleanly if it doesn't exist at all

    own = await _resolve_own_payer(db, current_user)
    if own is None or (assignment.payer_type, assignment.payer_id) != own:
        # Deliberately the same "not found" a stranger's ID would produce,
        # not a 403 — doesn't confirm to a prober that the ID is valid but
        # someone else's.
        raise ResourceNotFoundError("Fee assignment not found.")

    entries = await FeeAssignmentRepository(db).get_ledger(assignment_id)
    return [LedgerEntryOut.model_validate(e) for e in entries]


@router.get(
    "/me/{assignment_id}/challans",
    response_model=list[ChallanOut],
    summary="List challans for one of the current user's OWN assignments",
)
async def list_my_challans(
    assignment_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_VIEW)),
    db=Depends(get_db),
) -> list[ChallanOut]:
    service = FeeAssignmentService(db)
    assignment = await service.get(assignment_id)

    own = await _resolve_own_payer(db, current_user)
    if own is None or (assignment.payer_type, assignment.payer_id) != own:
        raise ResourceNotFoundError("Fee assignment not found.")

    challans = await ChallanService(db).list_for_assignment(assignment_id)
    return [ChallanOut.model_validate(c) for c in challans]


@router.post(
    "/{assignment_id}/payments",
    response_model=FeeAssignmentOut,
    status_code=201,
    summary="Manually record a payment (ADMIN-only override — the normal path is the student paying their own challan)",
)
async def record_payment(
    assignment_id: uuid.UUID,
    payload: RecordPaymentRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.PAYMENT_RECORD)),
    db=Depends(get_db),
) -> FeeAssignmentOut:
    service = FeeAssignmentService(db)
    await service.record_payment(assignment_id, payload, actor_id=current_user.id)
    assignment = await service.get(assignment_id)
    return await _to_assignment_out(db, assignment)


@router.post(
    "/{assignment_id}/installments/{ledger_entry_id}/post-late-fee",
    summary="Post this installment's currently-calculated late fee as a real, collectible Fine",
)
async def post_late_fee(
    assignment_id: uuid.UUID,
    ledger_entry_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_MANAGE)),
    db=Depends(get_db),
) -> dict:
    """Fixes a real gap: late_fee_per_day was calculated and DISPLAYED
    everywhere but never actually chargeable. This posts whatever
    portion hasn't already been posted as a Fine — safe to call
    repeatedly (e.g. weekly), never double-charges. See
    FeeAssignmentService.post_late_fee for the full reasoning."""
    fine = await FeeAssignmentService(db).post_late_fee(ledger_entry_id, actor_id=current_user.id)
    if fine is None:
        return {"posted": False, "message": "Nothing new to post for this installment."}
    return {"posted": True, "fine_id": str(fine.id), "amount": fine.amount}


@router.patch("/{assignment_id}/status", response_model=FeeAssignmentOut, summary="Change an assignment's status")
async def change_status(
    assignment_id: uuid.UUID,
    payload: AssignmentStatusUpdateRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_MANAGE)),
    db=Depends(get_db),
) -> FeeAssignmentOut:
    service = FeeAssignmentService(db)
    assignment = await service.change_status(assignment_id, status=payload.status, actor_id=current_user.id)
    return await _to_assignment_out(db, assignment)