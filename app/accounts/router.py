from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response

from app.accounts.models import EntryKind, ExpenseStatus, PaymentMethod
from app.accounts.schemas import (
    BookRow,
    CategoryIn,
    CategoryOut,
    CategoryUpdate,
    ConfigOut,
    ExpenseOut,
    IncomeIn,
    IncomeOut,
    ReasonIn,
    SummaryOut,
    VendorIn,
    VendorOut,
    VendorUpdate,
)
from app.accounts.service import AccountsService
from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission
from app.core.exceptions import ResourceNotFoundError
from app.core.storage import MAX_ATTACHMENT_BYTES, SAFE_SERVE_HEADERS, attachment_is_inline, get_file_path

router = APIRouter(prefix="/accounts", tags=["accounts"])
view = require_permission(Permissions.ACCOUNT_VIEW)
record = require_permission(Permissions.ACCOUNT_RECORD)
approve = require_permission(Permissions.ACCOUNT_APPROVE)


def _is_approver(user: CurrentUser) -> bool:
    return Permissions.ACCOUNT_APPROVE in user.permissions


@router.get("/config", response_model=ConfigOut, summary="The approval limit and the financial year")
async def config(current_user: CurrentUser = Depends(view)) -> ConfigOut:
    return ConfigOut(**AccountsService.config())


# ============================================================ categories
@router.get("/categories", response_model=list[CategoryOut], summary="Expense and income categories")
async def categories(kind: EntryKind | None = None, active_only: bool = False, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[CategoryOut]:
    return [CategoryOut(id=c.id, kind=c.kind, name=c.name, is_system=c.is_system, is_active=c.is_active) for c in await AccountsService(db).categories(kind, active_only)]


@router.post("/categories", response_model=CategoryOut, status_code=201, summary="Add a category (administration)")
async def add_category(payload: CategoryIn, current_user: CurrentUser = Depends(approve), db=Depends(get_db)) -> CategoryOut:
    c = await AccountsService(db).create_category(kind=payload.kind, name=payload.name, actor_id=current_user.id)
    return CategoryOut(id=c.id, kind=c.kind, name=c.name, is_system=c.is_system, is_active=c.is_active)


@router.patch("/categories/{category_id}", response_model=CategoryOut, summary="Rename or switch off a category (administration)")
async def edit_category(category_id: uuid.UUID, payload: CategoryUpdate, current_user: CurrentUser = Depends(approve), db=Depends(get_db)) -> CategoryOut:
    c = await AccountsService(db).update_category(category_id, name=payload.name, is_active=payload.is_active, actor_id=current_user.id)
    return CategoryOut(id=c.id, kind=c.kind, name=c.name, is_system=c.is_system, is_active=c.is_active)


# ============================================================ vendors
@router.get("/vendors", response_model=list[VendorOut], summary="Vendors, with how much has been spent with each")
async def vendors(q: str | None = None, active_only: bool = False, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[VendorOut]:
    return [VendorOut(**v) for v in await AccountsService(db).vendors(q, active_only)]


@router.post("/vendors", response_model=VendorOut, status_code=201, summary="Add a vendor")
async def add_vendor(payload: VendorIn, current_user: CurrentUser = Depends(record), db=Depends(get_db)) -> VendorOut:
    v = await AccountsService(db).create_vendor(payload.model_dump(), actor_id=current_user.id)
    return VendorOut(id=v.id, is_active=v.is_active, **payload.model_dump())


@router.patch("/vendors/{vendor_id}", response_model=VendorOut, summary="Edit or switch off a vendor")
async def edit_vendor(vendor_id: uuid.UUID, payload: VendorUpdate, current_user: CurrentUser = Depends(record), db=Depends(get_db)) -> VendorOut:
    service = AccountsService(db)
    await service.update_vendor(vendor_id, payload.model_dump(exclude_unset=True), actor_id=current_user.id)
    return VendorOut(**next(v for v in await service.vendors(None, False) if v["id"] == vendor_id))


# ============================================================ expenses
async def _read(files: list[UploadFile]) -> list[tuple[str, bytes, str | None]]:
    return [(f.filename or "file", await f.read(MAX_ATTACHMENT_BYTES + 1), f.content_type) for f in files]


@router.post("/expenses", response_model=ExpenseOut, status_code=201, summary="Record an expense, with receipts. Above the approval limit it waits for the administration")
async def add_expense(
    category_id: uuid.UUID = Form(...), title: str = Form(...), amount: Decimal = Form(...), expense_date: date = Form(...), payment_method: PaymentMethod = Form(...),
    vendor_id: uuid.UUID | None = Form(default=None), description: str | None = Form(default=None), reference_no: str | None = Form(default=None), files: list[UploadFile] = File(default=[]),
    current_user: CurrentUser = Depends(record), db=Depends(get_db),
) -> ExpenseOut:
    service = AccountsService(db)
    e = await service.create_expense(user_id=current_user.id, is_approver=_is_approver(current_user), files=await _read(files),
                                     data={"category_id": category_id, "title": title, "amount": amount, "expense_date": expense_date, "payment_method": payment_method,
                                           "vendor_id": vendor_id, "description": description, "reference_no": reference_no})
    return ExpenseOut(**await service.expense_detail(e.id))


@router.get("/expenses", response_model=list[ExpenseOut], summary="Expenses, filtered")
async def expenses(status: ExpenseStatus | None = None, category_id: uuid.UUID | None = None, vendor_id: uuid.UUID | None = None, from_date: date | None = None, to_date: date | None = None,
                   q: str | None = None, limit: int = 100, offset: int = 0, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[ExpenseOut]:
    return [ExpenseOut(**r) for r in await AccountsService(db).expenses(status=status, category_id=category_id, vendor_id=vendor_id, from_date=from_date, to_date=to_date, q=q, limit=limit, offset=offset)]


@router.get("/expenses/attachments/{attachment_id}/file", summary="Open a receipt")
async def receipt(attachment_id: uuid.UUID, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> Response:
    att = await AccountsService(db).attachment(attachment_id)
    path = get_file_path(att.storage_key)
    if path is None:
        raise ResourceNotFoundError("The file is no longer available.")
    return Response(content=path.read_bytes(), media_type=att.content_type,
                    headers={"Content-Disposition": f'{"inline" if attachment_is_inline(att.content_type) else "attachment"}; filename="{att.original_name}"', **SAFE_SERVE_HEADERS})


@router.get("/expenses/{expense_id}", response_model=ExpenseOut, summary="One expense")
async def expense(expense_id: uuid.UUID, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> ExpenseOut:
    return ExpenseOut(**await AccountsService(db).expense_detail(expense_id))


@router.post("/expenses/{expense_id}/approve", response_model=ExpenseOut, summary="Approve an expense that is above the limit (administration)")
async def approve_expense(expense_id: uuid.UUID, current_user: CurrentUser = Depends(approve), db=Depends(get_db)) -> ExpenseOut:
    service = AccountsService(db)
    await service.decide_expense(expense_id, admin_id=current_user.id, approve=True, note=None)
    return ExpenseOut(**await service.expense_detail(expense_id))


@router.post("/expenses/{expense_id}/reject", response_model=ExpenseOut, summary="Reject an expense (administration)")
async def reject_expense(expense_id: uuid.UUID, payload: ReasonIn, current_user: CurrentUser = Depends(approve), db=Depends(get_db)) -> ExpenseOut:
    service = AccountsService(db)
    await service.decide_expense(expense_id, admin_id=current_user.id, approve=False, note=payload.reason)
    return ExpenseOut(**await service.expense_detail(expense_id))


@router.post("/expenses/{expense_id}/void", response_model=ExpenseOut, summary="Void an expense entered by mistake: it stays on record but stops counting (administration)")
async def void_expense(expense_id: uuid.UUID, payload: ReasonIn, current_user: CurrentUser = Depends(approve), db=Depends(get_db)) -> ExpenseOut:
    service = AccountsService(db)
    await service.void_expense(expense_id, admin_id=current_user.id, reason=payload.reason)
    return ExpenseOut(**await service.expense_detail(expense_id))


# ============================================================ other income
@router.post("/income", response_model=IncomeOut, status_code=201, summary="Record money received other than student fees")
async def add_income(payload: IncomeIn, current_user: CurrentUser = Depends(record), db=Depends(get_db)) -> IncomeOut:
    service = AccountsService(db)
    i = await service.create_income(user_id=current_user.id, data=payload.model_dump())
    return IncomeOut(**next(r for r in await service.income_list(category_id=None, from_date=None, to_date=None, include_voided=True, q=None, limit=5, offset=0) if r["id"] == i.id))


@router.get("/income", response_model=list[IncomeOut], summary="Other income, filtered")
async def income(category_id: uuid.UUID | None = None, from_date: date | None = None, to_date: date | None = None, include_voided: bool = False, q: str | None = None,
                 limit: int = 100, offset: int = 0, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[IncomeOut]:
    return [IncomeOut(**r) for r in await AccountsService(db).income_list(category_id=category_id, from_date=from_date, to_date=to_date, include_voided=include_voided, q=q, limit=limit, offset=offset)]


@router.post("/income/{income_id}/void", response_model=IncomeOut, summary="Void an income entry made by mistake (administration)")
async def void_income(income_id: uuid.UUID, payload: ReasonIn, current_user: CurrentUser = Depends(approve), db=Depends(get_db)) -> IncomeOut:
    service = AccountsService(db)
    await service.void_income(income_id, admin_id=current_user.id, reason=payload.reason)
    return IncomeOut(**next(r for r in await service.income_list(category_id=None, from_date=None, to_date=None, include_voided=True, q=None, limit=500, offset=0) if r["id"] == income_id))


# ============================================================ the day book and the report
@router.get("/book", response_model=list[BookRow], summary="The day book: every approved expense and every income entry, newest first")
async def book(kind: EntryKind | None = None, from_date: date | None = None, to_date: date | None = None, q: str | None = None, limit: int = 200, offset: int = 0,
               current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[BookRow]:
    return [BookRow(**r) for r in await AccountsService(db).book(kind=kind, from_date=from_date, to_date=to_date, q=q, limit=limit, offset=offset)]


@router.get("/summary", response_model=SummaryOut, summary="Income versus expenses for a period, with fee collections, categories and a 12-month trend")
async def summary(from_date: date | None = None, to_date: date | None = None, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> SummaryOut:
    return SummaryOut(**await AccountsService(db).summary(from_date=from_date, to_date=to_date))