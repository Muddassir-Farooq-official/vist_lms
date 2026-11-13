from __future__ import annotations

import csv
import io
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response

from app.accounts.service import AccountsService
from app.auth.permissions import Permissions
from app.core.clock import institute_today
from app.core.csvsafe import safe_writer
from app.core.database import get_db
from app.core.dependencies import CurrentUser, require_permission
from app.core.exceptions import ValidationAppError
from app.reports.finance_service import BUCKETS, FinanceReportService

router = APIRouter(prefix="/finance-reports", tags=["finance reports"])
view = require_permission(Permissions.ACCOUNT_VIEW)
MAX_STATEMENT_BYTES = 2 * 1024 * 1024


def _csv(rows: list[list], header: list[str], filename: str) -> Response:
    buf = io.StringIO()
    w = safe_writer(buf, lineterminator="\r\n")
    w.writerow(header)
    w.writerows(rows)
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@router.get("/defaulters", summary="Students with overdue fees, aged 1-30 / 31-60 / 61-90 / 90+ days")
async def defaulters(program_id: uuid.UUID | None = None, semester: int | None = None, min_amount: float | None = None,
                     current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    return await FinanceReportService(db).defaulters(program_id=program_id, semester=semester, min_amount=min_amount)


@router.get("/defaulters.csv", summary="The defaulters list as a spreadsheet")
async def defaulters_csv(program_id: uuid.UUID | None = None, semester: int | None = None, min_amount: float | None = None,
                         current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> Response:
    r = await FinanceReportService(db).defaulters(program_id=program_id, semester=semester, min_amount=min_amount)
    rows = [[s["roll_number"], s["name"], s.get("father") or "", s["program"] or "", s["semester"] or "", s["phone"] or "", s["guardian_phone"] or "", s["oldest_due"], s["days_overdue"],
             *[s[b] for b in BUCKETS], s["overdue"], s["late_fee_accrued"], s["fines_pending"]] for s in r["students"]]
    return _csv(rows, ["Roll number", "Name", "Father / guardian", "Program", "Semester", "Phone", "Guardian phone", "Oldest due", "Days overdue", *[b + " days" for b in BUCKETS], "Total overdue", "Late fee accrued", "Fines pending"],
                f"defaulters-{r['as_of']}.csv")


@router.get("/collection", summary="Fee payments recorded in a period, per day and per cashier")
async def collection(from_date: date | None = None, to_date: date | None = None, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    today = institute_today()
    return await FinanceReportService(db).collection(from_date or today, to_date or from_date or today)


@router.get("/collection.csv", summary="The collection report as a spreadsheet")
async def collection_csv(from_date: date | None = None, to_date: date | None = None, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> Response:
    today = institute_today()
    r = await FinanceReportService(db).collection(from_date or today, to_date or from_date or today)
    rows = [[g["day"], g["cashier"], g["count"], " | ".join(f"{k} {v}" for k, v in g["by_method"].items()), g["received"], g["taken_back"], g["cash"], g["total"]] for g in r["rows"]]
    return _csv(rows, ["Day", "Cashier", "Payments", "By method", "Received", "Reversed or refunded", "Cash (net)", "Net total"], f"collection-{r['from_date']}-{r['to_date']}.csv")


@router.get("/payables", summary="Received purchases not yet paid, by vendor")
async def payables(current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    return await FinanceReportService(db).payables()


@router.post("/reconcile", summary="Match a bank statement (CSV) against the books")
async def reconcile(file: UploadFile = File(...), current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> dict:
    raw = await file.read(MAX_STATEMENT_BYTES + 1)
    if len(raw) > MAX_STATEMENT_BYTES:
        raise ValidationAppError("The statement is too large (2 MB at most).")
    return await FinanceReportService(db).reconcile(raw)


@router.get("/book.csv", summary="The day book as a spreadsheet")
async def book_csv(from_date: date | None = None, to_date: date | None = None, current_user: CurrentUser = Depends(view), db=Depends(get_db)) -> Response:
    rows = await AccountsService(db).book(kind=None, from_date=from_date, to_date=to_date, q=None, limit=500, offset=0)
    out = [[r["date"], r["reference"], r["kind"].value if hasattr(r["kind"], "value") else r["kind"], r["category"], r["title"], r["party"] or "", r["payment_method"] or "", r["status"], r["amount"]] for r in rows]
    return _csv(out, ["Date", "Reference", "Kind", "Category", "Title", "Party", "Method", "Status", "Amount"], "day-book.csv")


@router.get("/deductions.csv", summary="Salary deductions held or owed onward in a year, by month")
async def deductions_csv(year: int | None = None, current_user: CurrentUser = Depends(require_permission(Permissions.PAYROLL_VIEW)), db=Depends(get_db)) -> Response:
    r = await FinanceReportService(db).deductions(year or institute_today().year)
    heads = [h["name"] for h in r["heads"]]
    rows = [[m["month"], *[m["by_head"].get(h, 0) for h in heads], m["total"]] for m in r["months"]]
    rows.append(["Total", *[h["total"] for h in r["heads"]], r["total"]])
    return _csv(rows, ["Month", *heads, "Total"], f"salary-deductions-{r['year']}.csv")


@router.get("/salary-bank-sheet/{run_id}.csv", summary="The bank transfer sheet as a spreadsheet")
async def salary_bank_sheet_csv(run_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.PAYROLL_VIEW)), db=Depends(get_db)) -> Response:
    r = await FinanceReportService(db).salary_bank_sheet(run_id)
    rows = [[l["employee_code"], l["name"], l["account_title"], l["bank"], l["iban"], l["amount"]] for l in r["lines"]]
    return _csv(rows, ["Employee code", "Name", "Account title", "Bank", "IBAN / account", "Amount"], f"salary-bank-sheet-{r['month']:%Y-%m}.csv")


@router.get("/salary-bank-sheet/{run_id}", summary="Who to pay by bank transfer for one payroll run")
async def salary_bank_sheet(run_id: uuid.UUID, current_user: CurrentUser = Depends(require_permission(Permissions.PAYROLL_VIEW)), db=Depends(get_db)) -> dict:
    return await FinanceReportService(db).salary_bank_sheet(run_id)
