from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.auth.permissions import Permissions, Roles
from app.auth.schemas import CurrentUser
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.core.exceptions import AuthorizationError
from app.fees.assignment_router import _resolve_own_payer
from app.fees.assignment_service import ChallanService, FeeAssignmentService
from app.fees.schemas import ChallanOut, MarkPaidRequest, MarkUnpaidRequest

router = APIRouter(prefix="/fees/challans", tags=["fees"])


@router.post(
    "/{challan_id}/simulate-payment",
    response_model=ChallanOut,
    summary="Pay a challan (simulated — stands in for the real 1LINK flow until bank API access is live)",
)
async def simulate_challan_payment(
    challan_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_permission(Permissions.FEE_VIEW)),
    db=Depends(get_db),
) -> ChallanOut:
    challan_service = ChallanService(db)
    challan = await challan_service.get(challan_id)
    if settings.is_production:
        # A simulated payment marks a fee paid with no money moving. Never on the live system.
        raise AuthorizationError("Simulated payments are switched off on the live system. Record the real payment instead.")

    # Real security fix, not a style change: this used to allow ANY
    # real student to mark their OWN fee "paid" via a gateway that
    # always reports success — no money ever moved. Restricted to
    # ADMIN by default (for demoing the flow without a real bank
    # integration); a student can only reach this path if
    # settings.enable_student_payment_simulation is explicitly turned
    # on, which should only ever be true in a demo/test environment.
    assignment_service = FeeAssignmentService(db)
    assignment = await assignment_service.get(challan.fee_assignment_id)
    own = await _resolve_own_payer(db, current_user)
    is_owner = own is not None and (assignment.payer_type, assignment.payer_id) == own
    is_admin = Roles.ADMIN in current_user.roles
    if not (is_admin or (is_owner and settings.enable_student_payment_simulation)):
        raise AuthorizationError(
            "Self-service payment simulation is not enabled. Contact the accounts office to record this payment."
        )

    paid = await challan_service.simulate_payment(challan_id, actor_id=current_user.id)
    return ChallanOut.model_validate(paid)


@router.post(
    "/{challan_id}/mark-paid",
    response_model=ChallanOut,
    summary="Staff override: mark any challan paid (Accounts Officer / Admin — stands in for a real bank confirmation)",
)
async def mark_challan_paid(
    challan_id: uuid.UUID,
    payload: MarkPaidRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.PAYMENT_RECORD)),
    db=Depends(get_db),
) -> ChallanOut:
    """This is the day-to-day 'accountant confirms a payment' action while
    the real 1LINK gateway/webhook isn't live yet — gated by
    PAYMENT_RECORD, which currently only ADMIN and ACCOUNTS_OFFICER hold."""
    challan = await ChallanService(db).mark_paid_by_staff(
        challan_id, actor_id=current_user.id, note=payload.note,
        method=payload.method.value, reference=payload.reference, paid_on=payload.paid_on,
        late_fine=payload.late_fine, late_fine_reason=payload.late_fine_reason,
        may_remove_fine=True,      # the person confirming the payment may remove the fine: warned, with a reason, on record
    )
    return ChallanOut.model_validate(challan).model_copy(update={"late_fee_posted": getattr(challan, "late_fee_posted", 0.0) or None})


@router.post(
    "/{challan_id}/mark-unpaid",
    response_model=ChallanOut,
    summary="Staff override: revert a PAID challan back to unpaid (e.g. recorded in error)",
)
async def mark_challan_unpaid(
    challan_id: uuid.UUID,
    payload: MarkUnpaidRequest,
    current_user: CurrentUser = Depends(require_permission(Permissions.PAYMENT_REVERSE)),
    db=Depends(get_db),
) -> ChallanOut:
    """Reversing a recorded payment is an ADMIN-only correction action —
    deliberately a separate permission from PAYMENT_RECORD (mark-paid),
    which Accounts Officer also holds."""
    challan = await ChallanService(db).mark_unpaid(challan_id, actor_id=current_user.id, reason=payload.reason)
    return ChallanOut.model_validate(challan)