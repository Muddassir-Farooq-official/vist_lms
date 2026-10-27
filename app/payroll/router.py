from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.accounts.models import PaymentMethod
from app.accounts.schemas import ReasonIn
from app.auth.permissions import Permissions
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_permission
from app.core.exceptions import ResourceNotFoundError
from app.payroll.models import AdvanceStatus, PayItemCalc, PayItemKind
from app.payroll.payslip_pdf import render_payslip_pdf
from app.payroll.service import PayrollService

router = APIRouter(prefix="/payroll", tags=["payroll"])
view = require_permission(Permissions.PAYROLL_VIEW)
manage = require_permission(Permissions.PAYROLL_MANAGE)
approve = require_permission(Permissions.PAYROLL_APPROVE)
SECURITY = {"X-Content-Type-Options": "nosniff"}


class PayItemIn(BaseModel):
    employee_id: uuid.UUID
    kind: PayItemKind
    name: str = Field(min_length=2, max_length=80)
    calc: PayItemCalc = PayItemCalc.FIXED
    value: Decimal = Field(gt=0, le=10_000_000)
    valid_from: date | None = None
    valid_until: date | None = None


class PayItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    value: Decimal | None = Field(default=None, gt=0, le=10_000_000)
    is_active: bool | None = None
    valid_from: date | None = None
    valid_until: date | None = None


class AdvanceIn(BaseModel):
    employee_id: uuid.UUID
    amount: Decimal = Field(gt=0, le=10_000_000)
    monthly_recovery: Decimal = Field(gt=0, le=10_000_000)
    reason: str = Field(min_length=3, max_length=300)
    payment_method: PaymentMethod
    reference_no: str | None = Field(default=None, max_length=80)
    granted_on: date | None = None      # the day it is asked for (defaults to today); the payout date is recorded when it is paid


class RunIn(BaseModel):
    month: date
    working_days: int | None = Field(default=None, ge=1, le=31)     # empty = the institute's default (Settings)


class PayslipEdit(BaseModel):
    working_days: int | None = Field(default=None, ge=1, le=31)
    absent_days: Decimal | None = Field(default=None, ge=0, le=31)      # setting these overrides staff attendance
    late_marks: int | None = Field(default=None, ge=0, le=62)
    reset_attendance: bool = False                                      # go back to the figures from staff attendance
    unpaid_days: Decimal | None = Field(default=None, ge=0, le=31)
    adjustment: Decimal | None = Field(default=None, ge=-10_000_000, le=10_000_000)
    adjustment_note: str | None = Field(default=None, max_length=200)
    held: bool | None = None
    held_reason: str | None = Field(default=None, max_length=200)


class PayIn(BaseModel):
    payment_method: PaymentMethod
    paid_date: date
    reference_no: str | None = Field(default=None, max_length=80)


# ============================================================ pay items and advances
@router.get("/employees", summary="Search current staff, with their basic salary (for choosing who a pay item or advance is for)")
async def employees(q: str | None = None, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[dict]:
    return await PayrollService(db).employees(q)


@router.get("/items", summary="Recurring allowances and deductions")
async def items(employee_id: uuid.UUID | None = None, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[dict]:
    return await PayrollService(db).pay_items(employee_id)


@router.post("/items", status_code=201, summary="Add an allowance or deduction to an employee's pay")
async def add_item(payload: PayItemIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    service = PayrollService(db)
    i = await service.add_pay_item(payload.model_dump(), actor_id=current_user.id)
    return next(r for r in await service.pay_items(payload.employee_id) if r["id"] == i.id)


@router.patch("/items/{item_id}", summary="Change or switch off a pay item")
async def edit_item(item_id: uuid.UUID, payload: PayItemUpdate, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    service = PayrollService(db)
    i = await service.update_pay_item(item_id, payload.model_dump(exclude_unset=True), actor_id=current_user.id)
    return next(r for r in await service.pay_items(i.employee_id) if r["id"] == i.id)


@router.get("/advances", summary="Salary advances and what is still owed on each")
async def advances(employee_id: uuid.UUID | None = None, status: AdvanceStatus | None = None, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[dict]:
    return await PayrollService(db).advances(employee_id, status)


@router.post("/advances", status_code=201, summary="Ask for a salary advance for an employee; an administrator must approve it before it is paid")
async def request_advance(payload: AdvanceIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    service = PayrollService(db)
    a = await service.request_advance(actor_id=current_user.id, employee_id=payload.employee_id, amount=payload.amount, monthly_recovery=payload.monthly_recovery,
                                      reason=payload.reason, method=payload.payment_method, reference=payload.reference_no, granted_on=payload.granted_on)
    return next(r for r in await service.advances(payload.employee_id, None) if r["id"] == a.id)


class AdvanceDecisionIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=300)


@router.post("/advances/{advance_id}/decision", summary="Approve or reject a requested advance (administration; not your own request)")
async def decide_advance(advance_id: uuid.UUID, payload: AdvanceDecisionIn, current_user: CurrentUser = Depends(approve), db=Depends(get_db)) -> dict:
    service = PayrollService(db)
    a = await service.decide_advance(advance_id, admin_id=current_user.id, approve=payload.approve, note=payload.note)
    return next(r for r in await service.advances(a.employee_id, None) if r["id"] == a.id)


class AdvancePayIn(BaseModel):
    payment_method: PaymentMethod
    reference_no: str | None = Field(default=None, max_length=80)
    paid_on: date


@router.post("/advances/{advance_id}/pay", summary="Hand over an approved advance: it becomes an expense and is recovered from the next payslips")
async def pay_advance(advance_id: uuid.UUID, payload: AdvancePayIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    service = PayrollService(db)
    a = await service.pay_advance(advance_id, actor_id=current_user.id, method=payload.payment_method, reference=payload.reference_no, paid_on=payload.paid_on)
    return next(r for r in await service.advances(a.employee_id, None) if r["id"] == a.id)


@router.post("/advances/{advance_id}/cancel", summary="Call off an advance that has not been paid")
async def cancel_advance(advance_id: uuid.UUID, payload: ReasonIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    service = PayrollService(db)
    a = await service.cancel_advance(advance_id, actor_id=current_user.id, reason=payload.reason)
    return next(r for r in await service.advances(a.employee_id, None) if r["id"] == a.id)


# ============================================================ the monthly run
@router.get("/runs", summary="Payroll runs, newest month first")
async def runs(current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> list[dict]:
    return await PayrollService(db).runs()


@router.post("/runs", status_code=201, summary="Prepare payroll for a month: one payslip per employee, worked out from salary, pay items, bonuses and advances")
async def prepare(payload: RunIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    service = PayrollService(db)
    run, skipped = await service.prepare(payload.month, actor_id=current_user.id, working_days=payload.working_days)
    return {**await service.run_detail(run.id), "skipped": skipped}


@router.get("/runs/{run_id}", summary="One run with every payslip")
async def run_detail(run_id: uuid.UUID, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    return await PayrollService(db).run_detail(run_id)


@router.post("/runs/{run_id}/recalculate", summary="Work the draft out again from the latest salaries, pay items and bonuses (your unpaid days and adjustments are kept)")
async def recalculate(run_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    service = PayrollService(db)
    await service.recalculate(run_id, actor_id=current_user.id)
    return await service.run_detail(run_id)


@router.patch("/runs/{run_id}/payslips/{payslip_id}", summary="Set unpaid days, an adjustment, or hold a payslip back (draft only)")
async def edit_payslip(run_id: uuid.UUID, payslip_id: uuid.UUID, payload: PayslipEdit, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    service = PayrollService(db)
    await service.update_payslip(run_id, payslip_id, payload.model_dump(exclude_unset=True), actor_id=current_user.id)
    return await service.run_detail(run_id)


@router.post("/runs/{run_id}/submit", summary="Send a checked draft to the administration for approval (accounts office)")
async def submit_run(run_id: uuid.UUID, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    service = PayrollService(db)
    await service.submit(run_id, actor_id=current_user.id)
    return await service.run_detail(run_id)


@router.post("/runs/{run_id}/send-back", summary="Return a submitted run to the accounts office for corrections (administration)")
async def send_back_run(run_id: uuid.UUID, payload: ReasonIn, current_user: CurrentUser = Depends(approve), db=Depends(get_db)) -> dict:
    service = PayrollService(db)
    await service.send_back(run_id, admin_id=current_user.id, reason=payload.reason)
    return await service.run_detail(run_id)


@router.post("/runs/{run_id}/approve", summary="Approve a run the accounts office has submitted (administration)")
async def approve_run(run_id: uuid.UUID, current_user: CurrentUser = Depends(approve), db=Depends(get_db)) -> dict:
    service = PayrollService(db)
    await service.approve(run_id, admin_id=current_user.id)
    return await service.run_detail(run_id)


@router.post("/runs/{run_id}/pay", summary="Pay an approved run: it becomes one salary expense in the books, and everyone is sent their payslip")
async def pay_run(run_id: uuid.UUID, payload: PayIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    service = PayrollService(db)
    await service.pay(run_id, actor_id=current_user.id, method=payload.payment_method, reference=payload.reference_no, paid_date=payload.paid_date)
    return await service.run_detail(run_id)


@router.post("/runs/{run_id}/cancel", summary="Cancel a run that has not been paid")
async def cancel_run(run_id: uuid.UUID, payload: ReasonIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    service = PayrollService(db)
    await service.cancel(run_id, actor_id=current_user.id, reason=payload.reason)
    return await service.run_detail(run_id)


@router.post("/runs/{run_id}/reverse-payment", summary="Reverse a payment made by mistake (administration)")
async def reverse(run_id: uuid.UUID, payload: ReasonIn, current_user: CurrentUser = Depends(approve), db=Depends(get_db)) -> dict:
    service = PayrollService(db)
    await service.reverse_payment(run_id, admin_id=current_user.id, reason=payload.reason)
    return await service.run_detail(run_id)


# ============================================================ payslips
@router.get("/me/security", summary="My own security deposit: held now, the target, each month's cut and any pay-back")
async def my_security(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    out = await PayrollService(db).my_security(current_user.id)
    if out is None:
        raise ResourceNotFoundError("You are not on the staff list, so there is no security deposit.")
    return out


@router.get("/me", summary="My own payslips (paid months only)")
async def my_payslips(current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> list[dict]:
    return await PayrollService(db).my_payslips(current_user.id)


@router.get("/payslips/{payslip_id}", summary="One payslip in full (its owner once paid, or the accounts office)")
async def payslip(payslip_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> dict:
    return await PayrollService(db).payslip_detail(payslip_id, viewer_user_id=current_user.id, can_view_all=Permissions.PAYROLL_VIEW in current_user.permissions)


@router.get("/payslips/{payslip_id}/pdf", summary="A payslip as a PDF")
async def payslip_pdf(payslip_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user), db=Depends(get_db)) -> Response:
    p = await PayrollService(db).payslip_detail(payslip_id, viewer_user_id=current_user.id, can_view_all=Permissions.PAYROLL_VIEW in current_user.permissions)
    return Response(content=await run_in_threadpool(render_payslip_pdf, p), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="payslip-{p["employee_code"]}-{p["month"]:%Y-%m}.pdf"', **SECURITY})


class PaySettingIn(BaseModel):
    security_percent: Decimal | None = Field(default=None, ge=0, le=100)   # empty = the institute's %, 0 = none for this person
    security_opening: Decimal = Field(default=Decimal(0), ge=0, le=10_000_000)
    note: str | None = Field(default=None, max_length=200)


@router.get("/security", summary="Security deposits: what is held for each employee, and their own % if different")
async def security(current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    return await PayrollService(db).security_report()


@router.post("/runs/{run_id}/payslips/{payslip_id}/release", summary="Release a held payslip (administration): with the month if not yet paid, or pay it on its own if the month is already paid")
async def release_payslip(run_id: uuid.UUID, payslip_id: uuid.UUID, payload: PayIn, current_user: CurrentUser = Depends(approve), db=Depends(get_db)) -> dict:
    return await PayrollService(db).release_payslip(run_id, payslip_id, method=payload.payment_method, reference=payload.reference_no, paid_date=payload.paid_date, actor_id=current_user.id)


@router.post("/security/{employee_id}/release", summary="Pay a person's security deposit back (once); it becomes an expense in the books")
async def release_security(employee_id: uuid.UUID, payload: PayIn, current_user: CurrentUser = Depends(manage), db=Depends(get_db)) -> dict:
    return await PayrollService(db).release_security(employee_id, method=payload.payment_method, reference=payload.reference_no, paid_date=payload.paid_date, actor_id=current_user.id)


@router.put("/settings/{employee_id}", summary="One employee's own security % and the deposit already held before this system (administration)")
async def set_pay_setting(employee_id: uuid.UUID, payload: PaySettingIn, current_user: CurrentUser = Depends(approve), db=Depends(get_db)) -> dict:
    return await PayrollService(db).set_pay_setting(employee_id, payload.model_dump(), actor_id=current_user.id)


@router.get("/withholdings", summary="What salaries have held back in a year (security, deductions, advance recoveries), by head and month")
async def withholdings(year: int | None = None, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    from app.core.clock import institute_today

    return await PayrollService(db).withholdings(year or institute_today().year)