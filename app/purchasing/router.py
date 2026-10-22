from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.accounts.models import PaymentMethod
from app.accounts.schemas import ReasonIn
from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission
from app.core.exceptions import AuthorizationError
from app.core.storage import MAX_ATTACHMENT_BYTES
from app.purchasing.models import PurchaseStatus
from app.purchasing.schemas import OrderIn, PurchaseIn, PurchaseOut, PurchaseSummary, ReceiveIn
from app.purchasing.service import PurchasingService
from pydantic import BaseModel, Field

router = APIRouter(prefix="/purchases", tags=["purchasing"])
request_perm = require_permission(Permissions.PURCHASE_REQUEST)
view = require_permission(Permissions.ACCOUNT_VIEW)
record = require_permission(Permissions.ACCOUNT_RECORD)
approve = require_permission(Permissions.ACCOUNT_APPROVE)


class CancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


def _flags(user: CurrentUser) -> dict:
    return {"can_approve": Permissions.ACCOUNT_APPROVE in user.permissions, "can_record": Permissions.ACCOUNT_RECORD in user.permissions}


async def _one(service: PurchasingService, order_id: uuid.UUID, user: CurrentUser) -> PurchaseOut:
    return PurchaseOut(**await service.detail(order_id, viewer_id=user.id, can_see_all=Permissions.ACCOUNT_VIEW in user.permissions, **_flags(user)))


@router.post("", response_model=PurchaseOut, status_code=201, summary="Ask for something to be bought")
async def create(payload: PurchaseIn, current_user: CurrentUser = Depends(request_perm), db=Depends(get_db)) -> PurchaseOut:
    service = PurchasingService(db)
    o = await service.create(user_id=current_user.id, data=payload.model_dump())
    return await _one(service, o.id, current_user)


@router.get("/mine", response_model=list[PurchaseOut], summary="My own purchase requests")
async def mine(status: PurchaseStatus | None = None, limit: int = 100, offset: int = 0, current_user: CurrentUser = Depends(request_perm), db=Depends(get_db)) -> list[PurchaseOut]:
    rows = await PurchasingService(db).listing(viewer_id=current_user.id, status=status, q=None, mine_only=True, limit=limit, offset=offset, **_flags(current_user))
    return [PurchaseOut(**r) for r in rows]


@router.get("/summary", response_model=PurchaseSummary, summary="What is waiting at each step, and what is owed to vendors (accounts office)")
async def summary(current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> PurchaseSummary:
    return PurchaseSummary(**await PurchasingService(db).summary())


@router.get("", response_model=list[PurchaseOut], summary="Every purchase request (accounts office and administration)")
async def listing(status: PurchaseStatus | None = None, q: str | None = None, limit: int = 100, offset: int = 0, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[PurchaseOut]:
    rows = await PurchasingService(db).listing(viewer_id=current_user.id, status=status, q=q, mine_only=False, limit=limit, offset=offset, **_flags(current_user))
    return [PurchaseOut(**r) for r in rows]


@router.get("/{order_id}", response_model=PurchaseOut, summary="One purchase request (the person who asked, the accounts office, the administration)")
async def one(order_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> PurchaseOut:
    if Permissions.PURCHASE_REQUEST not in current_user.permissions and Permissions.ACCOUNT_VIEW not in current_user.permissions:
        raise AuthorizationError("You cannot view purchase requests.")
    return await _one(PurchasingService(db), order_id, current_user)


@router.post("/{order_id}/cancel", response_model=PurchaseOut, summary="Cancel a request (the person who asked, before it is decided; or the administration)")
async def cancel(order_id: uuid.UUID, payload: CancelIn, current_user: CurrentUser = Depends(request_perm), db=Depends(get_db)) -> PurchaseOut:
    service = PurchasingService(db)
    await service.cancel(order_id, actor_id=current_user.id, is_admin=Permissions.ACCOUNT_APPROVE in current_user.permissions, reason=payload.reason)
    return await _one(service, order_id, current_user)


@router.post("/{order_id}/approve", response_model=PurchaseOut, summary="Approve a request (administration)")
async def approve_it(order_id: uuid.UUID, current_user: CurrentUser = Depends(approve), db=Depends(get_db)) -> PurchaseOut:
    service = PurchasingService(db)
    await service.decide(order_id, admin_id=current_user.id, approve=True, note=None)
    return await _one(service, order_id, current_user)


@router.post("/{order_id}/approve-price", response_model=PurchaseOut, summary="Approve a higher price than estimated, so the purchase can be paid (administration)")
async def approve_price(order_id: uuid.UUID, current_user: CurrentUser = Depends(approve), db=Depends(get_db)) -> PurchaseOut:
    service = PurchasingService(db)
    await service.approve_price(order_id, admin_id=current_user.id)
    return await _one(service, order_id, current_user)


@router.post("/{order_id}/reject", response_model=PurchaseOut, summary="Reject a request, with a reason (administration)")
async def reject_it(order_id: uuid.UUID, payload: ReasonIn, current_user: CurrentUser = Depends(approve), db=Depends(get_db)) -> PurchaseOut:
    service = PurchasingService(db)
    await service.decide(order_id, admin_id=current_user.id, approve=False, note=payload.reason)
    return await _one(service, order_id, current_user)


@router.post("/{order_id}/order", response_model=PurchaseOut, summary="Place the order with a vendor (accounts office)")
async def place_order(order_id: uuid.UUID, payload: OrderIn, current_user: CurrentUser = Depends(record), db=Depends(get_db)) -> PurchaseOut:
    service = PurchasingService(db)
    await service.place_order(order_id, actor_id=current_user.id, vendor_id=payload.vendor_id, expected_delivery=payload.expected_delivery, note=payload.note)
    return await _one(service, order_id, current_user)


@router.post("/{order_id}/receive", response_model=PurchaseOut, summary="Record what actually arrived, at what price, and the invoice number (accounts office)")
async def receive(order_id: uuid.UUID, payload: ReceiveIn, current_user: CurrentUser = Depends(record), db=Depends(get_db)) -> PurchaseOut:
    service = PurchasingService(db)
    await service.receive(order_id, actor_id=current_user.id, invoice_no=payload.invoice_no, lines=[ln.model_dump() for ln in payload.lines])
    return await _one(service, order_id, current_user)


@router.post("/{order_id}/pay", response_model=PurchaseOut, summary="Pay the vendor: it becomes an expense in the books (accounts office)")
async def pay(order_id: uuid.UUID, payment_method: PaymentMethod = Form(...), paid_date: date = Form(...), reference_no: str | None = Form(default=None),
              files: list[UploadFile] = File(default=[]), current_user: CurrentUser = Depends(record), db=Depends(get_db)) -> PurchaseOut:
    service = PurchasingService(db)
    uploaded = [(f.filename or "file", await f.read(MAX_ATTACHMENT_BYTES + 1), f.content_type) for f in files]
    await service.pay(order_id, actor_id=current_user.id, method=payment_method, reference=reference_no, paid_date=paid_date, files=uploaded)
    return await _one(service, order_id, current_user)


@router.post("/{order_id}/reverse-payment", response_model=PurchaseOut, summary="Reverse a payment made by mistake: the expense is voided and the purchase goes back to 'received' (administration)")
async def reverse(order_id: uuid.UUID, payload: ReasonIn, current_user: CurrentUser = Depends(approve), db=Depends(get_db)) -> PurchaseOut:
    service = PurchasingService(db)
    await service.reverse_payment(order_id, admin_id=current_user.id, reason=payload.reason)
    return await _one(service, order_id, current_user)