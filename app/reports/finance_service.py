"""Finance reports for the accounts office: who owes what and for how long, what was collected each day and by whom,
what is owed to vendors, bank-statement matching, and the exports an accountant asks for."""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import EntryKind, Vendor
from app.accounts.service import AccountsService
from app.auth.models import User
from app.core.clock import institute_today, local_date
from app.core.exceptions import ResourceNotFoundError, ValidationAppError
from app.employees.models import Employee
from app.fees.assignment_service import FeeStatusCalculator, late_pause
from app.fees.models import AssignmentStatus, FeeAssignment, FeeLedgerEntry, FeeStructure, LedgerEntryType, PayerType
from app.fines.models import Fine, FinePayerType, FineStatus
from app.payroll.models import PayrollRun, Payslip
from app.payroll.service import PayrollService
from app.programs.models import Program
from app.purchasing.models import PurchaseOrder, PurchaseStatus
from app.students.models import Student, StudentGuardian

BUCKETS = ("1-30", "31-60", "61-90", "90+")


def _f(v) -> float:
    return round(float(v or 0), 2)


def _bucket(days: int) -> str:
    return "1-30" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+"


class FinanceReportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ defaulters with aging
    async def defaulters(self, *, program_id: uuid.UUID | None = None, semester: int | None = None, min_amount: float | None = None) -> dict:
        today = institute_today()
        stmt = (select(FeeAssignment, FeeStructure.late_fee_per_day, Student, Program.code)
                .join(FeeStructure, FeeStructure.id == FeeAssignment.fee_structure_id)
                .join(Student, Student.id == FeeAssignment.payer_id)
                .outerjoin(Program, Program.id == Student.program_id)
                .where(FeeAssignment.payer_type == PayerType.STUDENT, FeeAssignment.status == AssignmentStatus.ACTIVE))
        if program_id:
            stmt = stmt.where(Student.program_id == program_id)
        if semester:
            stmt = stmt.where(Student.current_semester == semester)
        found = (await self.session.execute(stmt)).all()
        entries: dict[uuid.UUID, list[FeeLedgerEntry]] = {}
        ids = [a.id for a, *_ in found]
        if ids:
            for e in (await self.session.execute(select(FeeLedgerEntry).where(FeeLedgerEntry.fee_assignment_id.in_(ids)))).scalars():
                entries.setdefault(e.fee_assignment_id, []).append(e)
        people: dict[uuid.UUID, dict] = {}
        for a, late_per_day, student, program_code in found:
            for r in FeeStatusCalculator.installments(entries.get(a.id, []), float(late_per_day) if late_per_day else None, as_of=today, pause=late_pause(a)):
                if r["status"] != "OVERDUE" or r["remaining"] <= 0.005 or r["due_date"] is None:
                    continue
                p = people.setdefault(student.id, {
                    "student_id": student.id, "name": f"{student.first_name} {student.last_name}", "roll_number": student.roll_number, "program": program_code,
                    "semester": student.current_semester, "phone": student.contact_no, "guardian_phone": None, "oldest_due": r["due_date"], "days_overdue": 0,
                    **{b: 0.0 for b in BUCKETS}, "overdue": 0.0, "late_fee_accrued": 0.0, "fines_pending": 0.0})
                days = (today - r["due_date"]).days
                p[_bucket(max(days, 1))] = round(p[_bucket(max(days, 1))] + r["remaining"], 2)
                p["overdue"] = round(p["overdue"] + r["remaining"], 2)
                p["late_fee_accrued"] = round(p["late_fee_accrued"] + max(r["late_fee"] - r["late_fee_posted"], 0), 2)
                if r["due_date"] < p["oldest_due"]:
                    p["oldest_due"] = r["due_date"]
        if people:
            for sid, phone in (await self.session.execute(select(StudentGuardian.student_id, StudentGuardian.contact_no).where(
                    StudentGuardian.student_id.in_(people), StudentGuardian.contact_no.is_not(None)).order_by(StudentGuardian.is_primary))).all():
                people[sid]["guardian_phone"] = people[sid]["guardian_phone"] or phone
            for sid, total in (await self.session.execute(select(Fine.payer_id, func.sum(Fine.amount)).where(
                    Fine.payer_type == FinePayerType.STUDENT, Fine.status == FineStatus.PENDING, Fine.payer_id.in_(people)).group_by(Fine.payer_id))).all():
                people[sid]["fines_pending"] = _f(total)
        rows = [p for p in people.values() if min_amount is None or p["overdue"] >= min_amount]
        for p in rows:
            p["days_overdue"] = max((today - p["oldest_due"]).days, 1)
        rows.sort(key=lambda p: -p["overdue"])
        totals = {b: round(sum(p[b] for p in rows), 2) for b in BUCKETS}
        totals["total"] = round(sum(p["overdue"] for p in rows), 2)
        return {"as_of": today, "students": rows, "totals": totals}

    # ------------------------------------------------------------------ daily collection
    async def collection(self, from_date: date, to_date: date) -> dict:
        if to_date < from_date:
            raise ValidationAppError("The end date is before the start date.")
        if (to_date - from_date).days > 366:
            raise ValidationAppError("Choose a period of one year or less.")
        from sqlalchemy.orm import aliased

        from app.fees import money as m

        # Payments received, and money taken back on the day it was taken back (reversed payments and refunds
        # paid out), so the net matches the accounts summary and the day book. A reversal is shown under the
        # method of the payment it cancels, so the cash total is what should really be in the drawer.
        original = aliased(FeeLedgerEntry)
        method = func.coalesce(FeeLedgerEntry.payment_method, original.payment_method)
        rows = (await self.session.execute(
            select(m.money_day(), FeeLedgerEntry.created_by, method, FeeLedgerEntry.entry_type, func.count(), func.sum(m.signed_amount()))
            .outerjoin(original, original.id == FeeLedgerEntry.related_entry_id)
            .where(m.moves_money(), m.money_day() >= from_date, m.money_day() <= to_date)
            .group_by(m.money_day(), FeeLedgerEntry.created_by, method, FeeLedgerEntry.entry_type))).all()
        names = {}
        uids = {r[1] for r in rows if r[1]}
        if uids:
            names = {u.id: u.username for u in (await self.session.execute(select(User).where(User.id.in_(uids)))).scalars()}
        groups: dict[tuple, dict] = {}
        by_method: dict[str, float] = {}
        received = taken_back = 0.0
        for day, cashier, pm, kind, count, amount in rows:
            pm = (pm.value if hasattr(pm, "value") else pm) or "CASH"
            g = groups.setdefault((day, cashier), {"day": day, "cashier": names.get(cashier, "Online / system") if cashier else "Online / system",
                                                   "count": 0, "by_method": {}, "cash": 0.0, "received": 0.0, "taken_back": 0.0, "total": 0.0})
            amount = _f(amount)
            if kind == LedgerEntryType.CREDIT:
                g["count"] += count
                g["received"] = round(g["received"] + amount, 2)
                received += amount
            else:
                g["taken_back"] = round(g["taken_back"] - amount, 2)
                taken_back -= amount
            g["by_method"][pm] = round(g["by_method"].get(pm, 0.0) + amount, 2)
            g["total"] = round(g["total"] + amount, 2)
            if pm == "CASH":
                g["cash"] = round(g["cash"] + amount, 2)
            by_method[pm] = round(by_method.get(pm, 0.0) + amount, 2)
        out = sorted(groups.values(), key=lambda g: (g["day"], g["cashier"]), reverse=True)
        return {"from_date": from_date, "to_date": to_date, "total": round(sum(g["total"] for g in out), 2), "received": round(received, 2),
                "taken_back": round(taken_back, 2), "by_method": by_method, "rows": out}

    # ------------------------------------------------------------------ owed to vendors
    async def payables(self) -> dict:
        today = institute_today()
        orders = (await self.session.execute(
            select(PurchaseOrder, Vendor.name, Vendor.phone).join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
            .where(PurchaseOrder.status == PurchaseStatus.RECEIVED, PurchaseOrder.paid_at.is_(None)).order_by(PurchaseOrder.received_at))).all()
        vendors: dict[str, dict] = {}
        for po, name, phone in orders:
            received = local_date(po.received_at) if po.received_at else today
            amount = _f(po.actual_total if po.actual_total is not None else po.estimated_total)
            v = vendors.setdefault(name, {"vendor": name, "phone": phone, "orders": [], "oldest_days": 0, "total": 0.0})
            v["orders"].append({"reference": f"PO-{po.serial_no}", "invoice_no": po.invoice_no, "amount": amount, "received_on": received})
            v["total"] = round(v["total"] + amount, 2)
            v["oldest_days"] = max(v["oldest_days"], (today - received).days)
        rows = sorted(vendors.values(), key=lambda v: -v["total"])
        return {"total": round(sum(v["total"] for v in rows), 2), "orders": len(orders), "vendors": rows}

    # ------------------------------------------------------------------ bank reconciliation
    @staticmethod
    def _parse_statement(raw: bytes) -> list[dict]:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValidationAppError("The statement is empty.")
        cols = {re.sub(r"[^a-z]", "", (c or "").lower()): c for c in reader.fieldnames}

        def pick(*names):
            return next((cols[n] for n in names if n in cols), None)

        c_date, c_ref = pick("date", "transactiondate", "valuedate", "postingdate"), pick("reference", "description", "narration", "details", "particulars", "ref")
        c_amt, c_cr, c_dr = pick("amount"), pick("credit", "deposit", "deposits"), pick("debit", "withdrawal", "withdrawals")
        if not c_date or not (c_amt or c_cr or c_dr):
            raise ValidationAppError("The statement needs a Date column and an Amount (or Credit / Debit) column.")

        def num(v) -> float:
            v = re.sub(r"[^\d.\-]", "", (v or "").replace(",", ""))
            return float(v) if v not in ("", "-", ".") else 0.0

        def day(v: str) -> date:
            v = (v or "").strip()
            for f in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y", "%m/%d/%Y"):
                try:
                    return datetime.strptime(v, f).date()
                except ValueError:
                    continue
            raise ValidationAppError(f"Cannot read the date '{v}' in the statement. Use YYYY-MM-DD or DD/MM/YYYY.")

        lines = []
        for row in reader:
            if not any((v or "").strip() for v in row.values()):
                continue
            amount = num(row.get(c_amt)) if c_amt else num(row.get(c_cr)) - num(row.get(c_dr))
            lines.append({"date": day(row.get(c_date)), "reference": (row.get(c_ref) or "").strip() if c_ref else "", "amount": round(amount, 2)})
        if not lines:
            raise ValidationAppError("The statement has no lines.")
        return lines

    async def reconcile(self, raw: bytes) -> dict:
        bank = self._parse_statement(raw)
        lo, hi = min(b["date"] for b in bank), max(b["date"] for b in bank)
        # Every non-cash entry in the period (the day book is read page by page; it used to stop at 500 rows).
        book, offset = [], 0
        while True:
            page = await AccountsService(self.session).book(kind=None, from_date=lo - timedelta(days=5), to_date=hi + timedelta(days=5), q=None, limit=500, offset=offset)
            book += [r for r in page if (r["payment_method"] or "CASH") != "CASH" and r["status"] not in ("REVERSED", "VOIDED")]
            if len(page) < 500:
                break
            offset += 500

        def squash(v) -> str:
            return re.sub(r"[^a-z0-9]", "", (v or "").lower())
        for r in book:
            r["_signed"] = round(r["amount"] if r["kind"] == EntryKind.INCOME else -r["amount"], 2)
        used: set[int] = set()
        matched, unmatched_bank = [], []
        for b in bank:
            hit, how = None, None
            narration = squash(b["reference"])
            for i, r in enumerate(book):                  # the slip / cheque / transaction number printed on the bank line wins
                ref = squash(r.get("bank_reference"))
                if i not in used and abs(r["_signed"] - b["amount"]) < 0.005 and len(ref) >= 4 and ref in narration:
                    hit, how = i, "bank reference and amount"
                    break
            if hit is None:
                for i, r in enumerate(book):              # else our receipt number, if the payer wrote it on the slip
                    if i not in used and abs(r["_signed"] - b["amount"]) < 0.005 and r["reference"] and squash(r["reference"]) in narration:
                        hit, how = i, "receipt number and amount"
                        break
            if hit is None:                               # else the same amount within three days, nearest date first; flagged when it is a guess
                near = sorted((abs((r["date"] - b["date"]).days), i) for i, r in enumerate(book)
                              if i not in used and abs(r["_signed"] - b["amount"]) < 0.005 and abs((r["date"] - b["date"]).days) <= 3)
                if near:
                    hit = near[0][1]
                    how = "amount and date" + (f" (check: {len(near)} entries have this amount)" if len(near) > 1 else "")
            if hit is None:
                unmatched_bank.append(b)
                continue
            used.add(hit)
            r = book[hit]
            matched.append({"bank": b, "book": {"kind": r["kind"].value if hasattr(r["kind"], "value") else r["kind"], "receipt": r["reference"], "date": r["date"], "amount": r["_signed"]}, "matched_on": how})
        unmatched_books = [{"date": r["date"], "kind": r["kind"].value if hasattr(r["kind"], "value") else r["kind"], "receipt": r["reference"], "title": r["title"],
                            "method": r["payment_method"], "reference": r.get("bank_reference") or "", "amount": r["_signed"]}
                           for i, r in enumerate(book) if i not in used and lo <= r["date"] <= hi]
        return {"bank_lines": len(bank), "matched": matched, "unmatched_bank": unmatched_bank, "unmatched_books": unmatched_books,
                "bank_total": round(sum(b["amount"] for b in bank), 2), "book_total": round(sum(r["_signed"] for r in book if lo <= r["date"] <= hi), 2), "from_date": lo, "to_date": hi}

    # ------------------------------------------------------------------ salary bank transfer sheet
    async def salary_bank_sheet(self, run_id: uuid.UUID) -> dict:
        run = await self.session.get(PayrollRun, run_id)
        if run is None:
            raise ResourceNotFoundError("Payroll run not found.")
        rows = (await self.session.execute(
            select(Payslip, Employee).join(Employee, Employee.id == Payslip.employee_id).where(Payslip.run_id == run_id, Payslip.held.is_(False)).order_by(Payslip.employee_name))).all()
        lines, without = [], []
        for slip, emp in rows:
            if _f(slip.net) <= 0:
                continue
            if emp.bank_iban:
                lines.append({"employee_code": slip.employee_code, "name": slip.employee_name, "account_title": emp.bank_account_title or slip.employee_name,
                              "bank": emp.bank_name or "", "iban": emp.bank_iban, "amount": _f(slip.net)})
            else:
                without.append(slip.employee_name)
        return {"run_id": run_id, "month": run.month, "status": run.status.value, "to_bank": len(lines), "bank_total": round(sum(l["amount"] for l in lines), 2),
                "lines": lines, "without_account": without}

    async def deductions(self, year: int) -> dict:
        return await PayrollService(self.session).withholdings(year)
