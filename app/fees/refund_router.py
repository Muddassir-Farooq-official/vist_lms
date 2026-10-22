"""Fee refunds: accounts office requests and pays; an administrator (not the requester) approves."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.fees import refund_models as _models  # noqa: F401  (registers the table)
from app.fees.models import PaymentMethod
from app.fees.refund_models import RefundKind, RefundStatus
from app.fees.refund_service import RefundService

router = APIRouter(prefix="/fees/refunds", tags=["fees"])
staff = require_permission(Permissions.PAYMENT_RECORD)   # the accounts office and administration (same as recording payments)
approver = require_permission(Permissions.FEE_REFUND_APPROVE)


class RefundIn(BaseModel):
    fee_assignment_id: uuid.UUID
    kind: RefundKind
    amount: float = Field(gt=0)
    write_off: float = Field(default=0, ge=0)
    reason: str = Field(min_length=10, max_length=500)


class DecisionIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=500)


class PayIn(BaseModel):
    method: PaymentMethod
    reference: str | None = Field(default=None, max_length=100)
    paid_on: date | None = None


def _out(r) -> dict:
    return {"id": r.id, "status": r.status.value, "amount": float(r.amount), "write_off": float(r.write_off),
            "voucher_number": r.voucher_number, "ledger_entry_id": r.ledger_entry_id}


@router.get("/position/{assignment_id}", summary="What can be refunded on this fee: credit balance and the most that can be written off")
async def position(assignment_id: uuid.UUID, current_user: CurrentUser = Depends(staff), db=Depends(get_db)) -> dict:
    return await RefundService(db).position(assignment_id)


@router.get("", summary="Refunds, newest first (filter by status)")
async def listing(status: RefundStatus | None = None, current_user: CurrentUser = Depends(staff), db=Depends(get_db)) -> list[dict]:
    return await RefundService(db).listing(status)


@router.post("", status_code=201, summary="Request a refund (accounts office / administration)")
async def request(payload: RefundIn, current_user: CurrentUser = Depends(staff), db=Depends(get_db)) -> dict:
    r = await RefundService(db).request(assignment_id=payload.fee_assignment_id, kind=payload.kind, amount=payload.amount,
                                        write_off=payload.write_off, reason=payload.reason, actor_id=current_user.id)
    return _out(r)


@router.post("/{refund_id}/decision", summary="Approve or reject a refund (administrator who did not request it)")
async def decide(refund_id: uuid.UUID, payload: DecisionIn, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    return _out(await RefundService(db).decide(refund_id, approve=payload.approve, note=payload.note, actor_id=current_user.id))


class CancelIn(BaseModel):
    reason: str = Field(min_length=5, max_length=300)


@router.post("/{refund_id}/cancel", summary="Call off a refund that has not been paid (administration); a write-off is put back")
async def cancel(refund_id: uuid.UUID, payload: CancelIn, current_user: CurrentUser = Depends(approver), db=Depends(get_db)) -> dict:
    return _out(await RefundService(db).cancel(refund_id, reason=payload.reason, actor_id=current_user.id))


@router.post("/{refund_id}/pay", summary="Record that an approved refund was paid out; creates the refund voucher")
async def pay(refund_id: uuid.UUID, payload: PayIn, current_user: CurrentUser = Depends(staff), db=Depends(get_db)) -> dict:
    if payload.method == PaymentMethod.ONE_BILL:
        from app.core.exceptions import ValidationAppError

        raise ValidationAppError("Refunds are paid by cash, cheque, bank deposit or online transfer.")
    return _out(await RefundService(db).pay(refund_id, method=payload.method.value, reference=payload.reference,
                                            paid_on=payload.paid_on, actor_id=current_user.id))