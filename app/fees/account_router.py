"""The account views people actually use: my own fees, a student's account (staff), the challan PDF, scholarships/discounts, previous dues."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, File, UploadFile
from starlette.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.permissions import Permissions, Roles
from app.core.database import get_db
from app.core.dependencies import CurrentUser, get_current_user, require_any_permission, require_any_role, require_permission
from app.core.exceptions import AuthorizationError, ResourceNotFoundError
from app.employees.repository import EmployeeRepository
from app.fees.account_service import AccountService
from app.fees.challan_pdf import render_challan_pdf
from app.fees.assignment_service import ChallanService, FeeAssignmentService
from app.fees.models import (
    FeeAssignment,
    FeeLedgerEntry,
    FeeStructure,
    FeeType,
    LedgerEntryType,
    PayerType,
    ScholarshipKind,
    ScholarshipStatus,
)
from app.fees.payment_models import PaymentChallan
from app.fees.opening_balance_service import OpeningBalanceService
from app.fees.scholarship_service import OPENING_CODE, ScholarshipService
from app.students.repository import StudentRepository

router = APIRouter(prefix="/fees", tags=["fee-accounts"])
_STAFF = require_any_role(Roles.ADMIN, Roles.ACCOUNTS_OFFICER)
_SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff"}
MAX_CSV_BYTES = 2 * 1024 * 1024


async def _own_payer(db, user: CurrentUser) -> tuple[PayerType, uuid.UUID] | None:
    student = await StudentRepository(db).get_by_user_id(user.id)
    if student is not None:
        return PayerType.STUDENT, student.id
    employee = await EmployeeRepository(db).get_by_user_id(user.id)
    return (PayerType.EMPLOYEE, employee.id) if employee is not None else None


# ============================================================ account statements
@router.get("/accounts/me", summary="My own fee account: every fee, its installments and challans, discounts, payments and what is still due")
async def my_account(current_user: CurrentUser = Depends(require_permission(Permissions.FEE_VIEW)), db=Depends(get_db)) -> dict:
    own = await _own_payer(db, current_user)
    if own is None:
        raise AuthorizationError("This account has no fee record.")
    return await AccountService(db).build(own[0], own[1], staff_view=False)


@router.get("/accounts/students/{student_id}", summary="One student's whole fee account (admin and accounts office)")
async def student_account(student_id: uuid.UUID, current_user: CurrentUser = Depends(_STAFF), db=Depends(get_db)) -> dict:
    return await AccountService(db).build(PayerType.STUDENT, student_id, staff_view=True)


# ============================================================ the challan, as a printable PDF
async def _parent_of(db, current_user: CurrentUser, assignment) -> bool:
    """A parent may open their own child's challans and receipts."""
    from app.fees.models import PayerType
    from app.parents.models import ParentStudentLink

    return (Roles.PARENT in current_user.roles and assignment.payer_type == PayerType.STUDENT
            and await db.get(ParentStudentLink, (current_user.id, assignment.payer_id)) is not None)


@router.get("/challans/{challan_id}/pdf", summary="Download a challan as a PDF (the person it belongs to, or staff)")
async def challan_pdf(challan_id: uuid.UUID, current_user: CurrentUser = Depends(require_any_permission(Permissions.FEE_VIEW, Permissions.PARENT_VIEW)), db=Depends(get_db)) -> Response:
    challan = await db.get(PaymentChallan, challan_id)
    if challan is None:
        raise ResourceNotFoundError("Challan not found.")
    assignment = await db.get(FeeAssignment, challan.fee_assignment_id)
    if not (Roles.ADMIN in current_user.roles or Roles.ACCOUNTS_OFFICER in current_user.roles):
        own = await _own_payer(db, current_user)
        if (own is None or (assignment.payer_type, assignment.payer_id) != own) and not await _parent_of(db, current_user, assignment):
            raise ResourceNotFoundError("Challan not found.")             # other people's challans are simply not there
    entry = await db.get(FeeLedgerEntry, challan.fee_ledger_entry_id)
    structure = await db.get(FeeStructure, assignment.fee_structure_id)
    fee_type = await db.get(FeeType, structure.fee_type_id)
    concession = sum(float(e.amount) for e in (await db.execute(select(FeeLedgerEntry).where(
        FeeLedgerEntry.fee_assignment_id == assignment.id, FeeLedgerEntry.entry_type == LedgerEntryType.CONCESSION,
        FeeLedgerEntry.installment_number == entry.installment_number))).scalars())
    text = "Previous dues" if fee_type.code == OPENING_CODE else f"Installment {entry.installment_number} of {structure.installment_count}"
    data = {"consumer_number": challan.consumer_number, "due_date": challan.due_date, "fee_name": structure.name, "installment_text": text,
            "payer": await AccountService(db).payer(assignment.payer_type, assignment.payer_id), "payable": float(challan.amount),
            "charged": float(entry.amount), "concession": concession, "status": challan.status.value, "paid_on": challan.paid_at,
            "generated_on": challan.generated_at or datetime.now(timezone.utc), "late_fee_per_day": float(structure.late_fee_per_day) if structure.late_fee_per_day else None}
    return Response(content=await run_in_threadpool(render_challan_pdf, data), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="challan-{challan.consumer_number}.pdf"', **_SECURITY_HEADERS})


@router.get("/receipts/{entry_id}/pdf", summary="Download the receipt for one recorded payment (the person who paid, or staff)")
async def receipt_pdf(entry_id: uuid.UUID, current_user: CurrentUser = Depends(require_any_permission(Permissions.FEE_VIEW, Permissions.PARENT_VIEW)), db=Depends(get_db)) -> Response:
    from app.core.config import settings
    from app.fees.receipt_pdf import render_receipt_pdf

    entry = await db.get(FeeLedgerEntry, entry_id)
    if entry is None or entry.entry_type not in (LedgerEntryType.CREDIT, LedgerEntryType.REFUND) or not entry.receipt_number:
        raise ResourceNotFoundError("Receipt not found.")
    assignment = await db.get(FeeAssignment, entry.fee_assignment_id)
    if not (Roles.ADMIN in current_user.roles or Roles.ACCOUNTS_OFFICER in current_user.roles):
        own = await _own_payer(db, current_user)
        if (own is None or (assignment.payer_type, assignment.payer_id) != own) and not await _parent_of(db, current_user, assignment):
            raise ResourceNotFoundError("Receipt not found.")
    structure = await db.get(FeeStructure, assignment.fee_structure_id)
    data = {"institute": settings.app_name, "receipt_number": entry.receipt_number, "paid_on": entry.paid_on, "recorded_on": entry.created_at.date(),
            "payer": await AccountService(db).payer(assignment.payer_type, assignment.payer_id), "fee_name": structure.name,
            "term_label": structure.term_label, "amount": float(entry.amount), "method": entry.payment_method,
            "reference": entry.payment_reference, "description": entry.description,
            "kind": "refund" if entry.entry_type == LedgerEntryType.REFUND else "receipt",
            "void": entry.entry_type == LedgerEntryType.CREDIT and await ChallanService(db).payment_is_reversed(entry.id)}
    return Response(content=await run_in_threadpool(render_receipt_pdf, data), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="receipt-{entry.receipt_number}.pdf"', **_SECURITY_HEADERS})


# ============================================================ scholarships and discounts
class ScholarshipIn(BaseModel):
    student_id: uuid.UUID
    name: str = Field(min_length=3, max_length=120)
    kind: ScholarshipKind
    value: float = Field(gt=0)
    fee_structure_id: uuid.UUID | None = None       # a single fee only (a discount); empty = every fee, now and in future (a scholarship)
    auto_apply: bool = True
    valid_from: date | None = None
    valid_until: date | None = None
    reason: str = Field(min_length=5, max_length=500)


@router.get("/scholarships", summary="Scholarships and discounts (admin and accounts office)")
async def list_scholarships(student_id: uuid.UUID | None = None, status: ScholarshipStatus | None = None, current_user: CurrentUser = Depends(_STAFF), db=Depends(get_db)) -> list[dict]:
    return await ScholarshipService(db).listing(student_id=student_id, status=status)


@router.post("/scholarships", status_code=201, summary="Grant a scholarship or discount to a student (admin)")
async def grant_scholarship(payload: ScholarshipIn, current_user: CurrentUser = Depends(require_permission(Permissions.SCHOLARSHIP_MANAGE)), db=Depends(get_db)) -> dict:
    s, applied = await ScholarshipService(db).grant(student_id=payload.student_id, name=payload.name.strip(), kind=payload.kind, value=payload.value, fee_structure_id=payload.fee_structure_id,
                                                    auto_apply=payload.auto_apply, valid_from=payload.valid_from, valid_until=payload.valid_until, reason=payload.reason.strip(), actor_id=current_user.id)
    return {"id": s.id, "status": s.status, "applied": applied}


@router.post("/scholarships/{scholarship_id}/end", summary="Stop a scholarship or discount (it stops applying to NEW charges; what was already given stays)")
async def end_scholarship(scholarship_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.SCHOLARSHIP_MANAGE)), db=Depends(get_db)) -> dict:
    s = await ScholarshipService(db).end(scholarship_id, actor_id=current_user.id)
    return {"id": s.id, "status": s.status}


# ============================================================ previous dues (opening balances)
class OpeningBalanceIn(BaseModel):
    student_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    due_date: date
    note: str | None = Field(default=None, max_length=300)


@router.post("/opening-balances", status_code=201, summary="Record what one student already owed before this system (accounts office)")
async def add_opening_balance(payload: OpeningBalanceIn, current_user: CurrentUser = Depends(require_permission(Permissions.FEE_MANAGE)), db=Depends(get_db)) -> dict:
    return await OpeningBalanceService(db).add(student_id=payload.student_id, amount=payload.amount, due_date=payload.due_date, note=payload.note, actor_id=current_user.id)


async def _csv(file: UploadFile) -> bytes:
    content = await file.read(MAX_CSV_BYTES + 1)
    if len(content) > MAX_CSV_BYTES:
        from app.core.exceptions import BusinessRuleViolationError

        raise BusinessRuleViolationError("That file is too large (2 MB at most).")
    return content


@router.post("/opening-balances/check", summary="Check a CSV of previous dues without saving anything (roll_number, amount, due_date, note)")
async def check_opening_csv(file: UploadFile = File(...), current_user: CurrentUser = Depends(require_permission(Permissions.FEE_MANAGE)), db=Depends(get_db)) -> list[dict]:
    return await OpeningBalanceService(db).check_csv(await _csv(file))


@router.post("/opening-balances/import", summary="Import a CSV of previous dues (every row must be valid)")
async def import_opening_csv(file: UploadFile = File(...), current_user: CurrentUser = Depends(require_permission(Permissions.FEE_MANAGE)), db=Depends(get_db)) -> dict:
    return await OpeningBalanceService(db).import_csv(await _csv(file), actor_id=current_user.id, commit=True)