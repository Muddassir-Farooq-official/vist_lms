from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.assets.service import AssetService
from app.auth.permissions import Permissions
from app.auth.schemas import CurrentUser
from app.core.database import get_db
from app.core.dependencies import require_permission

router = APIRouter(prefix="/assets", tags=["assets"])
view = require_permission(Permissions.ASSET_VIEW)
manage = require_permission(Permissions.ASSET_MANAGE)
approve = require_permission(Permissions.ASSET_APPROVE)

METHOD = "^(STRAIGHT_LINE|REDUCING_BALANCE|NONE)$"
CONDITION = "^(GOOD|FAIR|POOR|UNUSABLE)$"
STATUS = "^(IN_USE|IN_STORE|UNDER_REPAIR|MISSING)$"


# ------------------------------------------------------------------ schemas
class CategoryIn(BaseModel):
    code: str = Field(min_length=2, max_length=10)
    name: str = Field(min_length=2, max_length=100)
    method: str = Field(pattern=METHOD)
    useful_life_years: Decimal | None = Field(default=None, gt=0, le=100)
    rate_percent: Decimal | None = Field(default=None, gt=0, le=100)
    residual_percent: Decimal = Field(default=Decimal(0), ge=0, lt=100)


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    method: str | None = Field(default=None, pattern=METHOD)
    useful_life_years: Decimal | None = Field(default=None, gt=0, le=100)
    rate_percent: Decimal | None = Field(default=None, gt=0, le=100)
    residual_percent: Decimal | None = Field(default=None, ge=0, lt=100)
    is_active: bool | None = None


class LocationIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    building: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None


class TermsIn(BaseModel):
    method: str | None = Field(default=None, pattern=METHOD)                    # empty = the category's
    useful_life_years: Decimal | None = Field(default=None, gt=0, le=100)
    rate_percent: Decimal | None = Field(default=None, gt=0, le=100)
    residual_value: Decimal | None = Field(default=None, ge=0, le=10_000_000_000)


class AssetIn(TermsIn):
    tag: str | None = Field(default=None, max_length=30)                       # empty = automatic (VIST-IT-0001)
    name: str = Field(min_length=2, max_length=150)
    category_id: uuid.UUID
    quantity: int = Field(default=1, ge=1, le=500)
    description: str | None = Field(default=None, max_length=500)
    make_model: str | None = Field(default=None, max_length=150)
    serial_number: str | None = Field(default=None, max_length=100)
    location_id: uuid.UUID | None = None
    department: str | None = Field(default=None, max_length=100)
    custodian_employee_id: uuid.UUID | None = None
    condition: str = Field(default="GOOD", pattern=CONDITION)
    status: str = Field(default="IN_USE", pattern=STATUS)
    warranty_until: date | None = None
    source: str = Field(default="PURCHASE", pattern="^(PURCHASE|OPENING|DONATION|OTHER)$")
    acquired_on: date | None = None
    cost: Decimal = Field(ge=0, le=10_000_000_000)
    cost_is_estimate: bool = False
    depreciation_from: date | None = None
    vendor_id: uuid.UUID | None = None
    invoice_no: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=2000)


class AssetPatch(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    description: str | None = Field(default=None, max_length=500)
    make_model: str | None = Field(default=None, max_length=150)
    serial_number: str | None = Field(default=None, max_length=100)
    department: str | None = Field(default=None, max_length=100)
    warranty_until: date | None = None
    notes: str | None = Field(default=None, max_length=2000)
    location_id: uuid.UUID | None = None
    custodian_employee_id: uuid.UUID | None = None
    condition: str | None = Field(default=None, pattern=CONDITION)
    status: str | None = Field(default=None, pattern=STATUS)
    on: date | None = None
    note: str | None = Field(default=None, max_length=500)


class CorrectIn(TermsIn):
    reason: str = Field(min_length=10, max_length=500)
    category_id: uuid.UUID | None = None
    acquired_on: date | None = None
    cost: Decimal | None = Field(default=None, ge=0, le=10_000_000_000)
    cost_is_estimate: bool | None = None
    depreciation_from: date | None = None


class MaintenanceIn(BaseModel):
    summary: str = Field(min_length=3, max_length=250)
    on: date | None = None
    cost: Decimal | None = Field(default=None, ge=0, le=100_000_000)
    vendor_name: str | None = Field(default=None, max_length=150)
    note: str | None = Field(default=None, max_length=500)
    status: str | None = Field(default=None, pattern=STATUS)


class ImportIn(BaseModel):
    csv: str = Field(min_length=10, max_length=5_000_000)
    go_live: date | None = None
    commit: bool = False


class FromPurchaseIn(BaseModel):
    order_id: uuid.UUID
    purchase_item_id: uuid.UUID
    quantity: int | None = Field(default=None, ge=1, le=500)
    name: str | None = Field(default=None, max_length=150)
    category_id: uuid.UUID
    location_id: uuid.UUID | None = None
    department: str | None = Field(default=None, max_length=100)
    custodian_employee_id: uuid.UUID | None = None
    warranty_until: date | None = None
    make_model: str | None = Field(default=None, max_length=150)


class PeriodIn(BaseModel):
    period_end: date


class ReasonIn(BaseModel):
    reason: str = Field(min_length=10, max_length=300)


class DisposalIn(BaseModel):
    method: str = Field(pattern="^(SOLD|SCRAPPED|DONATED|LOST|WRITTEN_OFF)$")
    disposed_on: date | None = None
    proceeds: Decimal = Field(default=Decimal(0), ge=0, le=10_000_000_000)
    payment_method: str | None = Field(default=None, max_length=20)
    payment_reference: str | None = Field(default=None, max_length=80)
    buyer: str | None = Field(default=None, max_length=150)
    reason: str = Field(min_length=5, max_length=300)


class DecisionIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=300)


class CheckIn(BaseModel):
    title: str | None = Field(default=None, max_length=150)
    location_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)


class MarkIn(BaseModel):
    tag: str | None = Field(default=None, max_length=300)          # typed, or what a scanner read from the label
    asset_id: uuid.UUID | None = None
    found_location_id: uuid.UUID | None = None
    condition: str | None = Field(default=None, pattern=CONDITION)
    result: str | None = Field(default=None, pattern="^(FOUND|MISSING|NOT_CHECKED)$")
    note: str | None = Field(default=None, max_length=300)


class CloseIn(BaseModel):
    apply_moves: bool = False


def _brief(a) -> dict:
    return {"id": a.id, "tag": a.tag, "name": a.name, "status": a.status.value}


# ------------------------------------------------------------------ categories and places
@router.get("/categories")
async def categories(user: CurrentUser = Depends(view), db=Depends(get_db)):
    return await AssetService(db).categories()


@router.post("/categories", status_code=201)
async def create_category(body: CategoryIn, user: CurrentUser = Depends(manage), db=Depends(get_db)):
    c = await AssetService(db).create_category(body.model_dump(), actor_id=user.id)
    return {"id": c.id, "code": c.code, "name": c.name}


@router.post("/categories/suggested", status_code=201)
async def suggested_categories(user: CurrentUser = Depends(manage), db=Depends(get_db)):
    return {"added": await AssetService(db).add_suggested_categories(actor_id=user.id)}


@router.patch("/categories/{category_id}")
async def update_category(category_id: uuid.UUID, body: CategoryPatch, user: CurrentUser = Depends(manage), db=Depends(get_db)):
    c = await AssetService(db).update_category(category_id, body.model_dump(exclude_unset=True), actor_id=user.id)
    return {"id": c.id, "code": c.code, "name": c.name}


@router.get("/locations")
async def locations(user: CurrentUser = Depends(view), db=Depends(get_db)):
    return await AssetService(db).locations()


@router.post("/locations", status_code=201)
async def create_location(body: LocationIn, user: CurrentUser = Depends(manage), db=Depends(get_db)):
    loc = await AssetService(db).save_location(body.model_dump(exclude_unset=True), actor_id=user.id)
    return {"id": loc.id, "name": loc.name}


@router.patch("/locations/{location_id}")
async def update_location(location_id: uuid.UUID, body: LocationIn, user: CurrentUser = Depends(manage), db=Depends(get_db)):
    loc = await AssetService(db).save_location(body.model_dump(exclude_unset=True), actor_id=user.id, location_id=location_id)
    return {"id": loc.id, "name": loc.name}


# ------------------------------------------------------------------ register
@router.get("")
async def listing(q: str | None = Query(default=None, max_length=100), category_id: uuid.UUID | None = None, location_id: uuid.UUID | None = None,
                  status: str | None = Query(default=None, pattern="^(IN_USE|IN_STORE|UNDER_REPAIR|MISSING|DISPOSED)$"),
                  custodian_id: uuid.UUID | None = None, include_disposed: bool = False, limit: int = Query(default=200, ge=1, le=1000),
                  offset: int = Query(default=0, ge=0), user: CurrentUser = Depends(view), db=Depends(get_db)):
    return await AssetService(db).listing(q=q, category_id=category_id, location_id=location_id, status=status, custodian_id=custodian_id,
                                          include_disposed=include_disposed, limit=limit, offset=offset)


@router.get("/summary")
async def summary(user: CurrentUser = Depends(view), db=Depends(get_db)):
    return await AssetService(db).summary()


@router.get("/lookup")
async def lookup(tag: str = Query(min_length=2, max_length=300), user: CurrentUser = Depends(view), db=Depends(get_db)):
    return _brief(await AssetService(db).by_tag(tag))


@router.get("/people")
async def people(q: str = Query(min_length=2, max_length=60), user: CurrentUser = Depends(manage), db=Depends(get_db)):
    return await AssetService(db).people(q)


@router.post("", status_code=201)
async def create(body: AssetIn, user: CurrentUser = Depends(manage), db=Depends(get_db)):
    data = body.model_dump(exclude={"quantity"})
    made = await AssetService(db).create(data, actor_id=user.id, quantity=body.quantity)
    return [_brief(a) for a in made]


@router.post("/import")
async def import_csv(body: ImportIn, user: CurrentUser = Depends(manage), db=Depends(get_db)):
    return await AssetService(db).import_csv(body.csv, go_live=body.go_live, commit=body.commit, actor_id=user.id)


@router.get("/import/template.csv")
async def import_template(user: CurrentUser = Depends(view)):
    head = ",".join(AssetService.IMPORT_COLUMNS)
    sample = ",Steel almirah,FURN,Principal's office,Administration,,,,2015-08-01,18000,no,FAIR,,,,\n" \
             ",Projector (old),IT,Room 12,,,Epson EB-X41,,,,25000,yes,GOOD,,,,Value today; real cost unknown"
    return Response(content="﻿" + head + "\n" + sample + "\n", media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="assets-import-template.csv"'})


@router.get("/labels.pdf")
async def labels(ids: str | None = Query(default=None, max_length=40_000), location_id: uuid.UUID | None = None,
                 user: CurrentUser = Depends(view), db=Depends(get_db)):
    id_list = [uuid.UUID(x) for x in ids.split(",") if x.strip()] if ids else None
    pdf = await AssetService(db).labels_pdf(ids=id_list, location_id=location_id)
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="asset-labels.pdf"'})


# ------------------------------------------------------------------ purchases
@router.get("/from-purchases")
async def purchases(user: CurrentUser = Depends(manage), db=Depends(get_db)):
    return await AssetService(db).purchases_to_register()


@router.post("/from-purchases", status_code=201)
async def from_purchase(body: FromPurchaseIn, user: CurrentUser = Depends(manage), db=Depends(get_db)):
    line = body.model_dump(exclude={"order_id"})
    made = await AssetService(db).register_from_purchase(body.order_id, line, actor_id=user.id)
    return [_brief(a) for a in made]


# ------------------------------------------------------------------ depreciation
@router.get("/depreciation/runs")
async def runs(user: CurrentUser = Depends(view), db=Depends(get_db)):
    return await AssetService(db).runs()


@router.post("/depreciation/preview")
async def preview(body: PeriodIn, user: CurrentUser = Depends(manage), db=Depends(get_db)):
    return await AssetService(db).preview(body.period_end)


@router.post("/depreciation/runs", status_code=201)
async def post_run(body: PeriodIn, user: CurrentUser = Depends(manage), db=Depends(get_db)):
    r = await AssetService(db).post_run(body.period_end, actor_id=user.id)
    return {"id": r.id, "period_end": r.period_end, "total": float(r.total), "assets": r.assets_count}


@router.post("/depreciation/runs/{run_id}/reverse")
async def reverse_run(run_id: uuid.UUID, body: ReasonIn, user: CurrentUser = Depends(approve), db=Depends(get_db)):
    r = await AssetService(db).reverse_run(run_id, body.reason, actor_id=user.id)
    return {"id": r.id, "period_end": r.period_end, "reversed": True}


# ------------------------------------------------------------------ disposals
@router.get("/disposals")
async def disposals(status: str | None = Query(default=None, pattern="^(REQUESTED|APPROVED|REJECTED)$"), user: CurrentUser = Depends(view), db=Depends(get_db)):
    return await AssetService(db).disposals(status)


@router.post("/disposals/{disposal_id}/decide")
async def decide(disposal_id: uuid.UUID, body: DecisionIn, user: CurrentUser = Depends(approve), db=Depends(get_db)):
    d = await AssetService(db).decide_disposal(disposal_id, body.approve, body.note, actor_id=user.id)
    return {"id": d.id, "status": d.status, "book_value": float(d.book_value) if d.book_value is not None else None}


# ------------------------------------------------------------------ physical checks
@router.get("/checks")
async def checks(user: CurrentUser = Depends(view), db=Depends(get_db)):
    return await AssetService(db).checks()


@router.post("/checks", status_code=201)
async def start_check(body: CheckIn, user: CurrentUser = Depends(manage), db=Depends(get_db)):
    v = await AssetService(db).start_check(body.model_dump(), actor_id=user.id)
    return {"id": v.id, "title": v.title}


@router.get("/checks/{check_id}")
async def check_detail(check_id: uuid.UUID, user: CurrentUser = Depends(view), db=Depends(get_db)):
    return await AssetService(db).check_detail(check_id)


@router.post("/checks/{check_id}/mark")
async def mark(check_id: uuid.UUID, body: MarkIn, user: CurrentUser = Depends(manage), db=Depends(get_db)):
    return await AssetService(db).mark(check_id, body.model_dump(), actor_id=user.id)


@router.post("/checks/{check_id}/close")
async def close_check(check_id: uuid.UUID, body: CloseIn, user: CurrentUser = Depends(manage), db=Depends(get_db)):
    return await AssetService(db).close_check(check_id, apply_moves=body.apply_moves, actor_id=user.id)


# ------------------------------------------------------------------ one asset (keep these last: /{asset_id} would catch the paths above)
@router.get("/{asset_id}")
async def detail(asset_id: uuid.UUID, user: CurrentUser = Depends(view), db=Depends(get_db)):
    return await AssetService(db).detail(asset_id)


@router.patch("/{asset_id}")
async def update(asset_id: uuid.UUID, body: AssetPatch, user: CurrentUser = Depends(manage), db=Depends(get_db)):
    return _brief(await AssetService(db).update(asset_id, body.model_dump(exclude_unset=True), actor_id=user.id))


@router.post("/{asset_id}/correct")
async def correct(asset_id: uuid.UUID, body: CorrectIn, user: CurrentUser = Depends(approve), db=Depends(get_db)):
    changes = body.model_dump(exclude_unset=True, exclude={"reason"})
    return _brief(await AssetService(db).correct(asset_id, changes, body.reason, actor_id=user.id))


@router.post("/{asset_id}/maintenance", status_code=201)
async def maintenance(asset_id: uuid.UUID, body: MaintenanceIn, user: CurrentUser = Depends(manage), db=Depends(get_db)):
    e = await AssetService(db).add_maintenance(asset_id, body.model_dump(), actor_id=user.id)
    return {"id": e.id, "summary": e.summary}


@router.post("/{asset_id}/disposal", status_code=201)
async def request_disposal(asset_id: uuid.UUID, body: DisposalIn, user: CurrentUser = Depends(manage), db=Depends(get_db)):
    d = await AssetService(db).request_disposal(asset_id, body.model_dump(), actor_id=user.id)
    return {"id": d.id, "status": d.status}
