from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_permission
from app.core.exceptions import ResourceNotFoundError
from app.inventory.models import MovementKind
from app.inventory.service import InventoryService

router = APIRouter(prefix="/inventory", tags=["inventory"])
view = require_permission(Permissions.INVENTORY_VIEW)
manage = require_permission(Permissions.INVENTORY_MANAGE)
adjust_perm = require_permission(Permissions.INVENTORY_ADJUST)


class ItemIn(BaseModel):
    code: str | None = Field(default=None, max_length=30)
    name: str = Field(min_length=2, max_length=150)
    category: str | None = Field(default=None, max_length=60)
    unit: str | None = Field(default=None, max_length=20)
    sale_price: float | None = Field(default=None, ge=0, le=10_000_000)
    reorder_level: float | None = Field(default=None, ge=0, le=1_000_000)
    is_returnable: bool = False
    opening_quantity: float | None = Field(default=None, ge=0, le=1_000_000)
    opening_unit_cost: float | None = Field(default=None, ge=0, le=10_000_000)


class ItemPatch(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    category: str | None = Field(default=None, max_length=60)
    unit: str | None = Field(default=None, max_length=20)
    sale_price: float | None = Field(default=None, ge=0, le=10_000_000)
    reorder_level: float | None = Field(default=None, ge=0, le=1_000_000)
    is_returnable: bool | None = None
    is_active: bool | None = None


class NewItemIn(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    category: str | None = Field(default=None, max_length=60)
    unit: str | None = Field(default=None, max_length=20)
    sale_price: float | None = Field(default=None, ge=0)
    reorder_level: float | None = Field(default=None, ge=0)
    is_returnable: bool = False


class StockLineIn(BaseModel):
    purchase_item_id: uuid.UUID
    quantity: float | None = Field(default=None, gt=0, le=1_000_000)   # empty = everything not yet stocked
    stock_item_id: uuid.UUID | None = None                             # an existing item ...
    new_item: NewItemIn | None = None                                   # ... or a new one


class ReceiveIn(BaseModel):
    lines: list[StockLineIn] = Field(min_length=1, max_length=100)


class IssueIn(BaseModel):
    item_id: uuid.UUID
    quantity: float = Field(gt=0, le=1_000_000)
    party_type: str = Field(pattern="^(STUDENT|EMPLOYEE|DEPARTMENT)$")
    party_id: uuid.UUID | None = None
    department: str | None = Field(default=None, max_length=120)
    charge_mode: str = Field(default="FREE", pattern="^(FREE|PAID_NOW|ADD_TO_DUES)$")
    unit_price: float | None = Field(default=None, gt=0, le=10_000_000)
    payment_method: str | None = Field(default=None, max_length=20)
    payment_reference: str | None = Field(default=None, max_length=80)
    reason: str | None = Field(default=None, max_length=300)


class ReturnIn(BaseModel):
    quantity: float | None = Field(default=None, gt=0, le=1_000_000)
    reason: str | None = Field(default=None, max_length=300)
    refund: bool = False                                         # the issue was charged: give the money back / reduce the dues
    refund_method: str | None = Field(default=None, max_length=20)
    refund_reference: str | None = Field(default=None, max_length=80)


class AdjustIn(BaseModel):
    change: float = Field(ge=-1_000_000, le=1_000_000)       # + found / counted more, - damaged / lost / counted less
    reason: str = Field(min_length=5, max_length=300)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=5, max_length=300)


async def _row(service: InventoryService, movement_id: uuid.UUID) -> dict:
    return (await service._out_rows([await service.movement(movement_id)]))[0]


@router.get("/summary", summary="Stock value, low stock, what came in and went out this month")
async def summary(current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    return await InventoryService(db).summary()


@router.get("/items", summary="Items in the store, with quantity, average cost and value")
async def items(q: str | None = None, category: str | None = None, low_only: bool = False, include_inactive: bool = False,
                current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[dict]:
    return await InventoryService(db).items(q=q, category=category, low_only=low_only, include_inactive=include_inactive)


@router.post("/items", status_code=201, summary="Add an item (optionally with the stock already on the shelf)")
async def create_item(payload: ItemIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    service = InventoryService(db)
    item = await service.create_item(payload.model_dump(), actor_id=current_user.id)
    return next(i for i in await service.items(q=item.code, category=None, low_only=False, include_inactive=True) if i["id"] == item.id)


@router.patch("/items/{item_id}", summary="Rename, re-price, set the re-order level, or switch an item off")
async def update_item(item_id: uuid.UUID, payload: ItemPatch, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    service = InventoryService(db)
    item = await service.update_item(item_id, payload.model_dump(exclude_unset=True), actor_id=current_user.id)
    return next(i for i in await service.items(q=item.code, category=None, low_only=False, include_inactive=True) if i["id"] == item.id)


@router.get("/movements", summary="Every stock movement (newest first): the audit trail")
async def movements(item_id: uuid.UUID | None = None, kind: MovementKind | None = None, party_type: str | None = None, party_id: uuid.UUID | None = None,
                    q: str | None = None, from_date: date | None = None, to_date: date | None = None, limit: int = 200, offset: int = 0,
                    current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[dict]:
    return await InventoryService(db).movements(item_id=item_id, kind=kind, party_type=party_type, party_id=party_id, q=q,
                                                from_date=from_date, to_date=to_date, limit=limit, offset=offset)


@router.get("/holdings", summary="Returnable items still with someone")
async def holdings(current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[dict]:
    return await InventoryService(db).holdings()


@router.get("/purchases-to-stock", summary="Received purchase orders whose goods are not yet (fully) in stock")
async def purchases_to_stock(current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[dict]:
    return await InventoryService(db).purchases_to_stock()


@router.post("/purchases/{order_id}/receive", status_code=201, summary="Put the goods of a received purchase order into stock")
async def receive(order_id: uuid.UUID, payload: ReceiveIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    service = InventoryService(db)
    made = await service.receive_from_purchase(order_id, [line.model_dump() for line in payload.lines], actor_id=current_user.id)
    return await service._out_rows(made)


@router.post("/issue", status_code=201, summary="Issue items to a student, staff member or department: free, paid now (receipt), or added to their dues (a fine)")
async def issue(payload: IssueIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    service = InventoryService(db)
    m = await service.issue(payload.model_dump(), actor_id=current_user.id)
    return await _row(service, m.id)


@router.post("/movements/{movement_id}/return", status_code=201, summary="Items coming back from the person they were issued to")
async def return_items(movement_id: uuid.UUID, payload: ReturnIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    service = InventoryService(db)
    m = await service.return_items(movement_id, payload.quantity, reason=payload.reason, actor_id=current_user.id, refund=payload.refund,
                                   refund_method=payload.refund_method, refund_reference=payload.refund_reference)
    return await _row(service, m.id)


@router.post("/items/{item_id}/adjust", status_code=201, summary="Correct the stock after a count, damage or loss, with a reason (administration)")
async def adjust(item_id: uuid.UUID, payload: AdjustIn, current_user: CurrentUser = Depends(adjust_perm), db=Depends(get_db)) -> dict:
    service = InventoryService(db)
    m = await service.adjust(item_id, payload.change, reason=payload.reason, actor_id=current_user.id)
    return await _row(service, m.id)


@router.post("/movements/{movement_id}/reverse", status_code=201, summary="Undo a movement entered by mistake (administration). A sale's income is voided; unpaid dues are waived")
async def reverse(movement_id: uuid.UUID, payload: ReasonIn, current_user: CurrentUser = Depends(adjust_perm), db=Depends(get_db)) -> dict:
    service = InventoryService(db)
    m = await service.reverse(movement_id, reason=payload.reason, actor_id=current_user.id)
    return await _row(service, m.id)


@router.get("/movements/{movement_id}/receipt.pdf", summary="Receipt for an item sold from the store")
async def receipt(movement_id: uuid.UUID, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> Response:
    from app.core.config import settings
    from app.fees.receipt_pdf import render_receipt_pdf
    from app.inventory.models import StockItem

    service = InventoryService(db)
    m = await service.movement(movement_id)
    if not m.receipt_number:
        raise ResourceNotFoundError("This movement has no receipt (only items paid at the counter do).")
    item = await db.get(StockItem, m.item_id)
    name, _, ident = (m.party_name or "").partition(" (")
    data = {"institute": settings.app_name, "receipt_number": m.receipt_number, "paid_on": m.created_at.date(), "recorded_on": m.created_at.date(),
            "payer": {"name": name, "identifier": ident.rstrip(")") or None, "group": (m.party_type or "").title()},
            "fee_name": f"{item.name} x {float(-m.quantity):g} {item.unit} @ Rs. {float(m.unit_price):,.2f}", "term_label": None,
            "amount": float(m.charge_amount), "method": m.payment_method, "reference": m.payment_reference,
            "description": f"Store sale {item.code}", "kind": "sale"}
    return Response(content=await run_in_threadpool(render_receipt_pdf, data), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="receipt-{m.receipt_number}.pdf"'})


@router.get("/people", summary="Find a student or staff member to issue to (name, roll number or employee code)")
async def people(party_type: str, q: str, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> list[dict]:
    from app.core.pagination import PageParams
    from app.employees.repository import EmployeeRepository
    from app.students.repository import StudentRepository

    q = (q or "").strip()[:100]
    if len(q) < 2:
        return []
    page = PageParams(page=1, page_size=10)
    if party_type.upper() == "STUDENT":
        students, _ = await StudentRepository(db).search(page=page, query=q)
        return [{"id": s.id, "name": f"{s.first_name} {s.last_name}", "identifier": s.roll_number} for s in students]
    employees, _ = await EmployeeRepository(db).search(page=page, query=q, status=None, job_role_id=None)
    return [{"id": e.id, "name": f"{e.first_name} {e.last_name}", "identifier": e.employee_code} for e in employees]