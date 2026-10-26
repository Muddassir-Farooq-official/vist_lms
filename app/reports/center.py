"""Reports centre: every management report in one place.

Each report is declared once (REPORTS below) with its group, title, who may run it, its filters and a runner.
A runner returns a table: {"columns": [...], "rows": [...], "totals": {...}, "notes": [...], "subtitle": str}.
The Reports page renders any report from that shape, and the same result downloads as CSV, so the screen, the
download and the print-out always show the same numbers.

Money figures come from the same calculations the operational screens use (FeeStatusCalculator for fees, the
accounts day book rules for cash, payslip lines for payroll), never from a second formula.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Awaitable, Callable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permissions as P
from app.core.clock import institute_today, local_date
from app.core.config import settings
from app.core.exceptions import ResourceNotFoundError, ValidationAppError


def _f(v) -> float:
    return round(float(v or 0), 2)


def col(key: str, label: str, kind: str = "text", total: bool = False) -> dict:
    """kind: text | money | number | percent | date. total=True adds the column to the totals row."""
    return {"key": key, "label": label, "type": kind, "total": total}


@dataclass
class Report:
    key: str
    group: str
    title: str
    description: str
    permissions: tuple[str, ...]
    filters: list[dict]
    run: Callable[["ReportCenter", dict, Any], Awaitable[dict]]
    needs: tuple[str, ...] = field(default_factory=tuple)      # filters that must be given


# ============================================================== filter helpers
def f_period(default_days: int = 30) -> list[dict]:
    return [{"key": "from_date", "label": "From", "type": "date", "default": f"-{default_days}"},
            {"key": "to_date", "label": "To", "type": "date", "default": "today"}]


F_TERM = {"key": "term", "label": "Term", "type": "term"}
F_PROGRAM = {"key": "program_id", "label": "Program", "type": "program"}
F_SEMESTER = {"key": "semester", "label": "Semester", "type": "number"}
F_MONTH = {"key": "month", "label": "Month", "type": "month", "default": "this-month"}
F_YEAR = {"key": "year", "label": "Year", "type": "number", "default": "this-year"}
F_STUDENT = {"key": "student_id", "label": "Student", "type": "student"}


def _date(v, default: date | None = None) -> date | None:
    if v in (None, ""):
        return default
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError as exc:
        raise ValidationAppError(f"'{v}' is not a date (use YYYY-MM-DD).") from exc


def _uuid(v) -> uuid.UUID | None:
    if v in (None, ""):
        return None
    try:
        return uuid.UUID(str(v))
    except ValueError as exc:
        raise ValidationAppError("That choice is not valid.") from exc


def _int(v) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(v)
    except ValueError as exc:
        raise ValidationAppError(f"'{v}' is not a whole number.") from exc


def _period(params: dict, default_days: int = 30) -> tuple[date, date]:
    today = institute_today()
    to = _date(params.get("to_date"), today)
    frm = _date(params.get("from_date"), to - timedelta(days=default_days))
    if to < frm:
        raise ValidationAppError("The end date is before the start date.")
    if (to - frm).days > 3700:
        raise ValidationAppError("Choose a period of at most ten years.")
    return frm, to


def _month(params: dict) -> date:
    v = params.get("month")
    if v in (None, ""):
        return institute_today().replace(day=1)
    return _date(str(v)[:7] + "-01")


def _totals(columns: list[dict], rows: list[dict]) -> dict:
    out = {}
    for c in columns:
        if c.get("total"):
            out[c["key"]] = round(sum(float(r.get(c["key"]) or 0) for r in rows), 2)
    return out


def table(columns: list[dict], rows: list[dict], *, notes: list[str] | None = None, subtitle: str | None = None, totals: dict | None = None) -> dict:
    return {"columns": columns, "rows": rows, "totals": totals if totals is not None else _totals(columns, rows), "notes": notes or [], "subtitle": subtitle}


# ============================================================== the centre
class ReportCenter:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------ catalogue
    @staticmethod
    def allowed(report: Report, user) -> bool:
        return bool(set(report.permissions) & set(user.permissions))

    async def catalog(self, user) -> dict:
        from app.academics.terms import AcademicTermService
        from app.programs.models import Program

        reports = [r for r in REPORTS.values() if self.allowed(r, user)]
        need = {f["type"] for r in reports for f in r.filters}
        options: dict[str, list] = {}
        if "program" in need:
            options["program"] = [{"value": str(p.id), "label": f"{p.code} — {p.name}"} for p in
                                  (await self.session.execute(select(Program).order_by(Program.code))).scalars()]
        if "term" in need:
            options["term"] = [{"value": t.label, "label": t.label + ("" if t.status.value == "OPEN" else " (closed)")} for t in await AcademicTermService(self.session).all()]
        if "stock_item" in need:
            from app.inventory.models import StockItem

            options["stock_item"] = [{"value": str(i.id), "label": f"{i.code} — {i.name}"} for i in
                                     (await self.session.execute(select(StockItem).order_by(StockItem.code))).scalars()]
        if "payroll_run" in need:
            from app.payroll.models import PayrollRun

            options["payroll_run"] = [{"value": r.month.isoformat(), "label": r.month.strftime("%B %Y") + f" ({r.status.value.lower()})"} for r in
                                      (await self.session.execute(select(PayrollRun).where(PayrollRun.status.in_(("PAID", "APPROVED", "SUBMITTED", "DRAFT")))
                                                                  .order_by(PayrollRun.month.desc()).limit(36))).scalars()]
        if "expense_category" in need or "vendor" in need:
            from app.accounts.models import AccountCategory, EntryKind, Vendor

            options["expense_category"] = [{"value": str(c.id), "label": c.name} for c in (await self.session.execute(
                select(AccountCategory).where(AccountCategory.kind == EntryKind.EXPENSE).order_by(AccountCategory.name))).scalars()]
            options["vendor"] = [{"value": str(v.id), "label": v.name} for v in (await self.session.execute(select(Vendor).order_by(Vendor.name))).scalars()]
        if "asset_category" in need or "asset_location" in need:
            try:
                from app.assets.models import AssetCategory, AssetLocation

                options["asset_category"] = [{"value": str(c.id), "label": c.name} for c in (await self.session.execute(select(AssetCategory).order_by(AssetCategory.name))).scalars()]
                options["asset_location"] = [{"value": str(c.id), "label": c.name} for c in (await self.session.execute(select(AssetLocation).order_by(AssetLocation.name))).scalars()]
            except ImportError:  # pragma: no cover - the asset module is optional
                pass
        if "asset_check" in need:
            from app.assets.models import AssetVerification

            options["asset_check"] = [{"value": str(v.id), "label": f"{v.title} ({v.status.lower()})"} for v in (await self.session.execute(
                select(AssetVerification).order_by(AssetVerification.created_at.desc()).limit(100))).scalars()]
        if "cashier" in need:
            from app.auth.models import Permission, Role, RolePermission, User, UserRole

            users = (await self.session.execute(
                select(User).distinct().join(UserRole, UserRole.user_id == User.id).join(RolePermission, RolePermission.role_id == UserRole.role_id)
                .join(Permission, Permission.id == RolePermission.permission_id).where(Permission.code.in_((P.PAYMENT_RECORD, P.ACCOUNT_RECORD))))).scalars().all()
            names = await self._names({u.id for u in users})
            options["cashier"] = sorted([{"value": str(u.id), "label": names.get(u.id) or u.username} for u in users], key=lambda o: o["label"])
        if "fy" in need:
            start = int(settings.financial_year_start_month or 7)
            today = institute_today()
            y = today.year if today.month >= start else today.year - 1
            options["fy"] = [{"value": str(y - i), "label": f"{date(y - i, start, 1):%b %Y} \u2013 {date(y - i + 1, start, 1) - timedelta(days=1):%b %Y}"} for i in range(6)]
        groups: dict[str, list] = {}
        for r in reports:
            groups.setdefault(r.group, []).append({"key": r.key, "title": r.title, "description": r.description, "filters": r.filters, "needs": list(r.needs)})
        return {"groups": [{"name": g, "reports": rs} for g, rs in groups.items()], "options": options,
                "financial_year_start_month": settings.financial_year_start_month, "today": institute_today()}

    async def run(self, key: str, params: dict, user) -> dict:
        report = REPORTS.get(key)
        if report is None or not self.allowed(report, user):
            raise ResourceNotFoundError("Report not found.")
        missing = [f["label"] for f in report.filters if f["key"] in report.needs and params.get(f["key"]) in (None, "")]
        if missing:
            raise ValidationAppError("Choose: " + ", ".join(missing) + ".")
        out = await report.run(self, params, user)
        out.update({"key": report.key, "title": report.title, "group": report.group, "generated_on": institute_today()})
        return out

    # ------------------------------------------------------------ shared lookups
    async def _hod_teacher_ids(self, user) -> set | None:
        """A Head of Department sees only their department's classes (None = not limited)."""
        from app.core.scope import hod_department, teacher_ids_in

        dept = await hod_department(self.session, user)
        return None if dept is None else await teacher_ids_in(self.session, dept)

    async def _names(self, user_ids: set) -> dict:
        from app.accounts.service import AccountsService

        return await AccountsService(self.session)._names({u for u in user_ids if u})

    # ============================================================ FEES
    async def fee_position(self, params: dict, user) -> dict:
        from app.fees.assignment_service import FeeStatusCalculator, late_pause
        from app.fees.models import AssignmentStatus, FeeAssignment, FeeLedgerEntry, FeeStructure, FeeType, PayerType
        from app.programs.models import Program
        from app.students.models import Student

        group_by = params.get("group_by") or "program"
        stmt = (select(FeeAssignment, FeeStructure, FeeType.name, Student, Program.code)
                .join(FeeStructure, FeeStructure.id == FeeAssignment.fee_structure_id).join(FeeType, FeeType.id == FeeStructure.fee_type_id)
                .join(Student, Student.id == FeeAssignment.payer_id).outerjoin(Program, Program.id == Student.program_id)
                .where(FeeAssignment.payer_type == PayerType.STUDENT, FeeAssignment.status == AssignmentStatus.ACTIVE))
        if params.get("term"):
            stmt = stmt.where(FeeStructure.term_label == params["term"])
        if _uuid(params.get("program_id")):
            stmt = stmt.where(Student.program_id == _uuid(params["program_id"]))
        if _int(params.get("semester")):
            stmt = stmt.where(Student.current_semester == _int(params["semester"]))
        found = (await self.session.execute(stmt)).all()
        entries: dict = {}
        if found:
            ids = [a.id for a, *_ in found]
            for e in (await self.session.execute(select(FeeLedgerEntry).where(FeeLedgerEntry.fee_assignment_id.in_(ids)))).scalars():
                entries.setdefault(e.fee_assignment_id, []).append(e)
        groups: dict = {}
        for a, structure, type_name, st, prog in found:
            if group_by == "fee_head":
                k, label = type_name, type_name
            elif group_by == "term":
                k, label = structure.term_label, structure.term_label
            elif group_by == "structure":
                k, label = structure.id, f"{structure.name} ({structure.term_label})"
            else:
                k = (prog or "", st.current_semester or 0)
                label = (prog or "No program") + (f" · semester {st.current_semester}" if st.current_semester else "")
            s = FeeStatusCalculator.compute(entries.get(a.id, []), float(structure.late_fee_per_day) if structure.late_fee_per_day else None, pause=late_pause(a))
            g = groups.setdefault(k, {"group": label, "_students": set(), "billed": 0.0, "discount": 0.0, "payable": 0.0, "paid": 0.0,
                                      "outstanding": 0.0, "overdue": 0.0, "late_fee": 0.0})
            g["_students"].add(st.id)
            for out_key, in_key in (("billed", "total_charged"), ("discount", "total_concession"), ("payable", "total_payable"),
                                    ("paid", "total_paid"), ("outstanding", "total_remaining"), ("overdue", "overdue_principal"), ("late_fee", "accrued_fine")):
                g[out_key] = round(g[out_key] + s[in_key], 2)
        rows = []
        for k in sorted(groups, key=lambda x: str(x)):
            g = groups[k]
            g["students"] = len(g.pop("_students"))
            g["collected_pct"] = round(100 * g["paid"] / g["payable"], 1) if g["payable"] else None
            rows.append(g)
        labels = {"program": "Program and semester", "fee_head": "Fee head", "term": "Term", "structure": "Fee"}
        columns = [col("group", labels.get(group_by, "Group")), col("students", "Students", "number", True), col("billed", "Billed", "money", True),
                   col("discount", "Scholarships / discounts", "money", True), col("payable", "Payable", "money", True), col("paid", "Paid", "money", True),
                   col("outstanding", "Still owed", "money", True), col("overdue", "Overdue", "money", True), col("late_fee", "Late fee built up", "money", True),
                   col("collected_pct", "Collected %", "percent")]
        out = table(columns, rows, notes=["Active fees only: waived and cancelled fees are left out. Paid is net of reversed payments and refunds.",
                                         "Students are counted once per row; a student with several fees in one row counts once."])
        if out["totals"].get("payable"):
            out["totals"]["collected_pct"] = round(100 * out["totals"]["paid"] / out["totals"]["payable"], 1)
        out["totals"]["students"] = len({st.id for _a, _s, _t, st, _p in found})
        return out

    async def scholarships(self, params: dict, user) -> dict:
        from app.fees.models import ScholarshipStatus
        from app.fees.scholarship_service import ScholarshipService

        status = params.get("status") or None
        rows = await ScholarshipService(self.session).listing(student_id=None, status=ScholarshipStatus(status) if status else None)
        frm, to = _date(params.get("from_date")), _date(params.get("to_date"))
        out = []
        for s in rows:
            made = local_date(s["created_at"]) if s.get("created_at") else None
            if (frm and made and made < frm) or (to and made and made > to):
                continue
            out.append({"student": s["student_name"], "roll_number": s["roll_number"], "name": s["name"],
                        "value": (f"{s['value']:g}%" if str(s["kind"]).endswith("PERCENT") else f"Rs. {s['value']:,.0f}") + (" (one fee)" if s["fee_structure_id"] else ""),
                        "covers": f"{s['valid_from']}" + (f" to {s['valid_until']}" if s["valid_until"] else " onwards"), "given": s["given_so_far"],
                        "status": "Running" if str(s["status"]).endswith("ACTIVE") else "Ended", "reason": s["reason"], "granted_on": made})
        columns = [col("student", "Student"), col("roll_number", "Roll no."), col("name", "Scholarship / discount"), col("value", "Amount"),
                   col("covers", "Covers"), col("given", "Given so far", "money", True), col("status", "Status"), col("granted_on", "Granted on", "date"), col("reason", "Reason")]
        return table(columns, out, notes=["“Given so far” is what has actually been taken off fees (concession entries in the fee ledger)."])

    async def previous_dues(self, params: dict, user) -> dict:
        from app.fees.opening_balance_service import OpeningBalanceService

        rows = await OpeningBalanceService(self.session).listing(q=params.get("q") or None, limit=5000)
        if params.get("only_unpaid") in ("1", "true", True):
            rows = [r for r in rows if r["remaining"] > 0.005]
        out = [{"student": r["student_name"], "roll_number": r["roll_number"], "note": r["note"], "due_date": r["due_date"], "amount": r["amount"],
                "cancelled": r["cancelled"], "paid": r["paid"], "remaining": r["remaining"], "status": r["status"].title()} for r in rows]
        columns = [col("student", "Student"), col("roll_number", "Roll no."), col("note", "Note"), col("due_date", "Pay by", "date"),
                   col("amount", "Brought in", "money", True), col("cancelled", "Cancelled", "money", True), col("paid", "Paid", "money", True),
                   col("remaining", "Still owed", "money", True), col("status", "Status")]
        return table(columns, out)

    async def student_statement(self, params: dict, user) -> dict:
        from app.fees.account_service import AccountService
        from app.fees.assignment_service import FeeStatusCalculator, late_pause
        from app.fees.models import FeeLedgerEntry, FeeStructure, LedgerEntryType, PayerType
        from app.fees.repository import FeeAssignmentRepository

        sid = _uuid(params.get("student_id"))
        acct = await AccountService(self.session).build(PayerType.STUDENT, sid, staff_view=True)
        repo = FeeAssignmentRepository(self.session)
        lines = []
        for a in await repo.list_for_payer(PayerType.STUDENT, sid):
            structure = await self.session.get(FeeStructure, a.fee_structure_id)
            for e in await repo.get_ledger(a.id):
                day = e.paid_on or e.due_date if e.entry_type == LedgerEntryType.DEBIT and not FeeStatusCalculator._is_reversal(e) else (e.paid_on or local_date(e.created_at))
                if e.entry_type == LedgerEntryType.DEBIT and not FeeStatusCalculator._is_reversal(e):
                    day = local_date(e.created_at)
                charge = credit = 0.0
                if e.entry_type in (LedgerEntryType.DEBIT, LedgerEntryType.REFUND, LedgerEntryType.CREDIT_OUT):
                    charge = _f(e.amount)
                else:
                    credit = _f(e.amount)
                what = {LedgerEntryType.DEBIT: "Charge", LedgerEntryType.CONCESSION: "Discount", LedgerEntryType.CREDIT: "Payment", LedgerEntryType.REFUND: "Refund paid back",
                        LedgerEntryType.CREDIT_IN: "Credit brought from another fee", LedgerEntryType.CREDIT_OUT: "Credit moved to another fee"}[e.entry_type]
                if e.entry_type == LedgerEntryType.DEBIT and (e.description or "").startswith("Write-off cancelled"):
                    what = "Write-off cancelled"
                elif FeeStatusCalculator._is_reversal(e):
                    what = "Payment reversed"
                elif e.entry_type == LedgerEntryType.DEBIT and e.installment_number:
                    what = f"Installment {e.installment_number}" + (f" (due {e.due_date})" if e.due_date else "")
                lines.append({"date": day, "fee": structure.name if structure else "", "entry": what + ("" if a.status.value == "ACTIVE" else f" [fee {a.status.value.lower()}]"),
                              "reference": e.receipt_number or "", "charge": charge, "credit": credit, "_t": e.created_at, "_active": a.status.value == "ACTIVE"})
        lines.sort(key=lambda r: (r["date"], r["_t"]))
        bal = 0.0
        for r in lines:
            if r.pop("_active"):
                bal = round(bal + r["charge"] - r["credit"], 2)
            r["balance"] = bal
            r.pop("_t")
        p, s = acct["payer"], acct["summary"]
        columns = [col("date", "Date", "date"), col("fee", "Fee"), col("entry", "Entry"), col("reference", "Receipt / voucher"),
                   col("charge", "Charge", "money", True), col("credit", "Payment / discount", "money", True), col("balance", "Balance", "money")]
        notes = [f"Still owed: Rs. {s['remaining']:,.2f} (overdue Rs. {s['overdue']:,.2f}). Unpaid fines: Rs. {s['fines_unpaid']:,.2f}."]
        if s.get("credit_balance"):
            notes.append(f"Paid more than owed: Rs. {s['credit_balance']:,.2f} can be refunded.")
        notes.append("Lines of waived or cancelled fees are listed but not counted in the balance.")
        out = table(columns, lines, notes=notes, subtitle=f"{p['name']} · {p.get('identifier') or ''} · {p.get('group') or ''}")
        out["totals"]["balance"] = bal
        return out

    async def fines_summary(self, params: dict, user) -> dict:
        from app.fines.models import Fine, FinePaymentReversal, FineStatus

        frm, to = _period(params, 30)
        issued_day = func.date(func.timezone(settings.institute_timezone, Fine.issued_at))
        issued = (await self.session.execute(select(Fine.source, func.count(), func.sum(Fine.amount)).where(
            issued_day >= frm, issued_day <= to, Fine.status.not_in((FineStatus.PENDING_APPROVAL, FineStatus.REJECTED))).group_by(Fine.source))).all()
        paid_day = func.coalesce(Fine.paid_on, func.date(Fine.resolved_at))
        paid = (await self.session.execute(select(Fine.source, func.count(), func.sum(Fine.amount)).where(
            Fine.status == FineStatus.PAID, paid_day >= frm, paid_day <= to).group_by(Fine.source))).all()
        resolved_day = func.date(func.timezone(settings.institute_timezone, Fine.resolved_at))
        waived = (await self.session.execute(select(Fine.source, func.count(), func.sum(Fine.amount)).where(
            Fine.status == FineStatus.WAIVED, resolved_day >= frm, resolved_day <= to).group_by(Fine.source))).all()
        pending = (await self.session.execute(select(Fine.source, func.count(), func.sum(Fine.amount)).where(Fine.status == FineStatus.PENDING).group_by(Fine.source))).all()
        rows: dict = {}
        for name, data in (("issued", issued), ("paid", paid), ("waived", waived), ("pending", pending)):
            for src, n, amt in data:
                r = rows.setdefault(src.value, {"source": src.value.replace("_", " ").title(), "issued_n": 0, "issued": 0.0, "paid": 0.0, "waived": 0.0, "pending_n": 0, "pending": 0.0})
                if name == "pending":
                    r["pending_n"], r["pending"] = n, _f(amt)
                elif name == "issued":
                    r["issued_n"], r["issued"] = n, _f(amt)
                else:
                    r[name] = _f(amt)
        for rv, src in (await self.session.execute(select(FinePaymentReversal, Fine.source).join(Fine, Fine.id == FinePaymentReversal.fine_id))).all():
            r = rows.setdefault(src.value, {"source": src.value.replace("_", " ").title(), "issued_n": 0, "issued": 0.0, "paid": 0.0, "waived": 0.0, "pending_n": 0, "pending": 0.0})
            if rv.paid_on and frm <= rv.paid_on <= to:
                r["paid"] = round(r["paid"] + _f(rv.amount), 2)
            if frm <= rv.reversed_on <= to:
                r["paid"] = round(r["paid"] - _f(rv.amount), 2)
        columns = [col("source", "Kind of fine / charge"), col("issued_n", "Issued (count)", "number", True), col("issued", "Issued", "money", True),
                   col("paid", "Collected (net)", "money", True), col("waived", "Waived", "money", True), col("pending_n", "Unpaid now (count)", "number", True),
                   col("pending", "Unpaid now", "money", True)]
        return table(columns, sorted(rows.values(), key=lambda r: r["source"]),
                     notes=["Includes late fees, hostel rent and store charges, which are all collected as fines.", "“Unpaid now” is today's position, whatever the period."])

    # ============================================================ ACCOUNTS
    def _fy(self, params: dict) -> tuple[date, date, list[date]]:
        start_month = int(settings.financial_year_start_month or 7)
        today = institute_today()
        y = _int(params.get("fy_start")) or (today.year if today.month >= start_month else today.year - 1)
        first = date(y, start_month, 1)
        months = []
        d = first
        for _ in range(12):
            months.append(d)
            d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        return first, d - timedelta(days=1), months

    async def income_statement(self, params: dict, user) -> dict:
        from app.accounts.models import AccountCategory, Expense, ExpenseStatus, IncomeEntry
        from app.fees import money as fee_money
        from app.fines.models import Fine, FinePaymentReversal, FineStatus

        first, last, months = self._fy(params)
        mk = lambda d: f"{d.year:04d}-{d.month:02d}"                                            # noqa: E731
        keys = [mk(m) for m in months]
        cats = {c.id: c.name for c in (await self.session.execute(select(AccountCategory))).scalars()}
        lines: dict[tuple, dict] = {}

        def put(section: str, name: str, month_key: str, amount) -> None:
            r = lines.setdefault((section, name), {"section": section, "line": name, **{k: 0.0 for k in keys}})
            if month_key in r:
                r[month_key] = round(r[month_key] + _f(amount), 2)

        fm = func.to_char(fee_money.money_day(), "YYYY-MM")
        for m, amt in (await self.session.execute(select(fm, func.sum(fee_money.signed_amount())).where(
                fee_money.moves_money(), fee_money.money_day() >= first, fee_money.money_day() <= last).group_by(fm))).all():
            put("Income", "Student / staff fees (net of reversals and refunds)", m, amt)
        paid_day = func.coalesce(Fine.paid_on, func.date(Fine.resolved_at))
        fnm = func.to_char(paid_day, "YYYY-MM")
        for m, amt in (await self.session.execute(select(fnm, func.sum(Fine.amount)).where(Fine.status == FineStatus.PAID, paid_day >= first, paid_day <= last).group_by(fnm))).all():
            put("Income", "Fines, hostel and store charges", m, amt)
        for rv in (await self.session.execute(select(FinePaymentReversal))).scalars():
            for day, amount in ((rv.paid_on, rv.amount), (rv.reversed_on, -float(rv.amount))):
                if day and first <= day <= last:
                    put("Income", "Fines, hostel and store charges", mk(day), amount)
        im = func.to_char(IncomeEntry.income_date, "YYYY-MM")
        for cat, m, amt in (await self.session.execute(select(IncomeEntry.category_id, im, func.sum(IncomeEntry.amount)).where(
                IncomeEntry.voided_at.is_(None), IncomeEntry.income_date >= first, IncomeEntry.income_date <= last).group_by(IncomeEntry.category_id, im))).all():
            put("Income", cats.get(cat, "Other income"), m, amt)
        em = func.to_char(Expense.expense_date, "YYYY-MM")
        for cat, m, amt in (await self.session.execute(select(Expense.category_id, em, func.sum(Expense.amount)).where(
                Expense.status == ExpenseStatus.APPROVED, Expense.expense_date >= first, Expense.expense_date <= last).group_by(Expense.category_id, em))).all():
            put("Expenses", cats.get(cat, "Other"), m, amt)
        # Fixed assets. The books are kept on a cash basis, so equipment paid for through a purchase order is already in
        # Expenses in full. To see the surplus after depreciation, that capital spending is added back and replaced by
        # the depreciation of the year, and the book value of assets disposed of is taken off (their sale money is income).
        adjust: dict[str, dict] = {}

        def adj(name: str, month_key: str, amount) -> None:
            r = adjust.setdefault(name, {"section": "Fixed assets", "line": name, **{k: 0.0 for k in keys}})
            if month_key in r:
                r[month_key] = round(r[month_key] + _f(amount), 2)

        try:
            from app.assets.models import Asset, AssetDisposal, DepreciationEntry
            from app.purchasing.models import PurchaseOrder
        except ImportError:  # pragma: no cover
            Asset = None
        if Asset is not None:
            xm = func.to_char(Expense.expense_date, "YYYY-MM")
            for m, amt in (await self.session.execute(
                    select(xm, func.sum(Asset.cost)).join(PurchaseOrder, PurchaseOrder.id == Asset.purchase_order_id).join(Expense, Expense.id == PurchaseOrder.expense_id)
                    .where(Expense.status == ExpenseStatus.APPROVED, Expense.expense_date >= first, Expense.expense_date <= last).group_by(xm))).all():
                adj("Add back: fixed assets bought (already in expenses)", m, amt)
            dm = func.to_char(DepreciationEntry.period_end, "YYYY-MM")
            for m, amt in (await self.session.execute(select(dm, func.sum(DepreciationEntry.amount)).where(
                    DepreciationEntry.period_end >= first, DepreciationEntry.period_end <= last, DepreciationEntry.reversed_at.is_(None)).group_by(dm))).all():
                adj("Less: depreciation of fixed assets", m, -_f(amt))
            om = func.to_char(AssetDisposal.disposed_on, "YYYY-MM")
            for m, amt in (await self.session.execute(select(om, func.sum(AssetDisposal.book_value)).where(
                    AssetDisposal.status == "APPROVED", AssetDisposal.disposed_on >= first, AssetDisposal.disposed_on <= last).group_by(om))).all():
                adj("Less: book value of assets disposed of", m, -_f(amt))

        def total_row(section: str, label: str, sign: dict[str, int], extra: list | None = None) -> dict:
            r = {"section": section, "line": label, "_bold": True}
            for k in keys:
                r[k] = round(sum(sign.get(x["section"], 0) * x[k] for x in list(lines.values()) + (extra or [])), 2)
            return r

        rows = []
        for section in ("Income", "Expenses"):
            sec = sorted([r for r in lines.values() if r["section"] == section], key=lambda r: -sum(r[k] for k in keys))
            if not sec:
                continue
            rows += sec
            rows.append(total_row(section, f"Total {section.lower()}", {section: 1}))
        rows.append(total_row("Result", "Surplus / (deficit) (cash basis)", {"Income": 1, "Expenses": -1}))
        notes = ["Cash basis: fees and fines count in the month the money moved; expenses in the month they are dated.",
                 "Fees billed but not yet paid are not income here; see Fee position for what is owed."]
        if any(any(r[k] for k in keys) for r in adjust.values()):
            rows += list(adjust.values())
            rows.append(total_row("Result", "Surplus / (deficit) after depreciation", {"Income": 1, "Expenses": -1, "Fixed assets": 1}, list(adjust.values())))
            notes.append("After depreciation: equipment bought is spread over its life instead of counting in full in the month it was paid for.")
        for r in rows:
            r["total"] = round(sum(r[k] for k in keys), 2)
        columns = [col("section", "Section"), col("line", "Line")] + [col(k, m.strftime("%b %Y"), "money") for k, m in zip(keys, months)] + [col("total", "Year", "money")]
        return table(columns, rows, totals={}, subtitle=f"Financial year {first:%b %Y} – {last:%b %Y}", notes=notes)

    async def _cash_before(self, day: date) -> float:
        """Net cash (method CASH) recorded before `day`, by the same rules as the day book."""
        from sqlalchemy.orm import aliased

        from app.accounts.models import Expense, ExpenseStatus, IncomeEntry, PaymentMethod
        from app.fees import money as fee_money
        from app.fees.models import FeeLedgerEntry
        from app.fines.models import Fine, FinePaymentReversal, FineStatus

        total = 0.0
        total -= _f((await self.session.execute(select(func.sum(Expense.amount)).where(
            Expense.status == ExpenseStatus.APPROVED, Expense.payment_method == PaymentMethod.CASH, Expense.expense_date < day))).scalar_one())
        total += _f((await self.session.execute(select(func.sum(IncomeEntry.amount)).where(
            IncomeEntry.voided_at.is_(None), IncomeEntry.payment_method == PaymentMethod.CASH, IncomeEntry.income_date < day))).scalar_one())
        original = aliased(FeeLedgerEntry)
        method = func.coalesce(FeeLedgerEntry.payment_method, original.payment_method, "CASH")
        total += _f((await self.session.execute(select(func.sum(fee_money.signed_amount())).outerjoin(original, original.id == FeeLedgerEntry.related_entry_id)
                                                .where(fee_money.moves_money(), method == "CASH", fee_money.money_day() < day))).scalar_one())
        paid_day = func.coalesce(Fine.paid_on, func.date(Fine.resolved_at))
        total += _f((await self.session.execute(select(func.sum(Fine.amount)).where(
            Fine.status == FineStatus.PAID, func.coalesce(Fine.payment_method, "CASH") == "CASH", paid_day < day))).scalar_one())
        for rv in (await self.session.execute(select(FinePaymentReversal).where(func.coalesce(FinePaymentReversal.payment_method, "CASH") == "CASH"))).scalars():
            if rv.paid_on and rv.paid_on < day:
                total += _f(rv.amount)
            if rv.reversed_on < day:
                total -= _f(rv.amount)
        return round(total, 2)

    async def _cash_rows(self, frm: date, to: date) -> list[dict]:
        from app.accounts.models import EntryKind
        from app.accounts.service import AccountsService

        rows, offset, svc = [], 0, AccountsService(self.session)
        while True:
            page = await svc.book(kind=None, from_date=frm, to_date=to, q=None, limit=500, offset=offset)
            rows += [r for r in page if (str(r["payment_method"]).upper().replace("PAYMENTMETHOD.", "") or "CASH") == "CASH" and r["status"] != "VOIDED"]
            if len(page) < 500:
                break
            offset += 500
        for r in rows:
            r["_in"] = r["kind"] == EntryKind.INCOME
        return rows

    async def cash_book(self, params: dict, user) -> dict:
        frm, to = _period(params, 7)
        rows = await self._cash_rows(frm, to)
        names = await self._names({r.get("by") for r in rows})
        cashier = _uuid(params.get("cashier_id"))
        view = params.get("view") or "detail"
        opening = await self._cash_before(frm) if cashier is None else 0.0
        rows.sort(key=lambda r: (r["date"], str(r["reference"])))
        if cashier is not None:
            rows = [r for r in rows if r.get("by") == cashier]
        if view == "daily":
            days: dict = {}
            bal = opening
            for r in rows:
                d = days.setdefault(r["date"], {"date": r["date"], "opening": 0.0, "received": 0.0, "paid": 0.0, "closing": 0.0, "entries": 0})
                amt = _f(r["amount"])
                if r["_in"]:
                    d["received"] = round(d["received"] + amt, 2)
                else:
                    d["paid"] = round(d["paid"] + amt, 2)
                d["entries"] += 1
            out = []
            for k in sorted(days):
                d = days[k]
                d["opening"] = round(bal, 2)
                bal = round(bal + d["received"] - d["paid"], 2)
                d["closing"] = bal
                out.append(d)
            columns = [col("date", "Date", "date"), col("entries", "Entries", "number", True), col("opening", "Opening cash", "money"),
                       col("received", "Received", "money", True), col("paid", "Paid out", "money", True), col("closing", "Closing cash", "money")]
            res = table(columns, out, subtitle=f"Opening cash on {frm}: Rs. {opening:,.2f}" if cashier is None else None)
            res["totals"]["closing"] = bal
            return res
        bal, out = opening, []
        for r in rows:
            amt = _f(r["amount"])
            rec, paid = (amt, 0.0) if r["_in"] else (0.0, amt)
            if rec < 0:                                             # a reversed receipt is cash going back out
                rec, paid = 0.0, -rec
            bal = round(bal + rec - paid, 2)
            out.append({"date": r["date"], "reference": r["reference"], "particulars": r["title"], "party": r["party"] or "", "category": r["category"],
                        "received": rec, "paid": paid, "balance": bal if cashier is None else None, "by": names.get(r.get("by"), "")})
        columns = [col("date", "Date", "date"), col("reference", "Voucher / receipt"), col("particulars", "Particulars"), col("party", "From / to"),
                   col("category", "Head"), col("received", "Received", "money", True), col("paid", "Paid out", "money", True)] + \
                  ([col("balance", "Cash balance", "money")] if cashier is None else []) + [col("by", "Recorded by")]
        notes = ["Cash only: payments by bank, cheque or online are in the day book, not here."]
        if cashier is not None:
            notes.append("Filtered to one person: shows what they took in and paid out; there is no running balance for one person.")
        res = table(columns, out, notes=notes, subtitle=f"Opening cash on {frm}: Rs. {opening:,.2f} · closing on {to}: Rs. {bal:,.2f}" if cashier is None else None)
        if cashier is None:
            res["totals"]["balance"] = bal
        return res

    async def expense_register(self, params: dict, user) -> dict:
        from app.accounts.models import AccountCategory, Expense, ExpenseStatus, Vendor
        from app.accounts.service import expense_ref

        frm, to = _period(params, 30)
        stmt = select(Expense).where(Expense.status == ExpenseStatus.APPROVED, Expense.expense_date >= frm, Expense.expense_date <= to)
        if _uuid(params.get("category_id")):
            stmt = stmt.where(Expense.category_id == _uuid(params["category_id"]))
        if _uuid(params.get("vendor_id")):
            stmt = stmt.where(Expense.vendor_id == _uuid(params["vendor_id"]))
        expenses = list((await self.session.execute(stmt.order_by(Expense.expense_date, Expense.serial_no))).scalars())
        cats = {c.id: c.name for c in (await self.session.execute(select(AccountCategory))).scalars()}
        vendors = {v.id: v.name for v in (await self.session.execute(select(Vendor))).scalars()}
        group = params.get("group_by") or ""
        if group in ("category", "vendor", "month"):
            g: dict = {}
            for e in expenses:
                k = cats.get(e.category_id, "?") if group == "category" else vendors.get(e.vendor_id, "No vendor") if group == "vendor" else e.expense_date.strftime("%Y-%m")
                r = g.setdefault(k, {"group": k, "count": 0, "amount": 0.0})
                r["count"] += 1
                r["amount"] = round(r["amount"] + _f(e.amount), 2)
            columns = [col("group", {"category": "Category", "vendor": "Vendor", "month": "Month"}[group]), col("count", "Expenses", "number", True), col("amount", "Amount", "money", True)]
            return table(columns, sorted(g.values(), key=lambda r: -r["amount"]))
        names = await self._names({e.recorded_by for e in expenses} | {e.decided_by for e in expenses})
        rows = [{"date": e.expense_date, "reference": expense_ref(e), "category": cats.get(e.category_id, "?"), "vendor": vendors.get(e.vendor_id, ""),
                 "title": e.title, "method": e.payment_method.value.replace("_", " ").title(), "reference_no": e.reference_no or "", "amount": _f(e.amount),
                 "recorded_by": names.get(e.recorded_by, ""), "approved_by": names.get(e.decided_by, ""), "source": e.source.value.title()} for e in expenses]
        columns = [col("date", "Date", "date"), col("reference", "Reference"), col("category", "Category"), col("vendor", "Vendor"), col("title", "For"),
                   col("method", "Paid by"), col("reference_no", "Cheque / bank ref."), col("amount", "Amount", "money", True), col("source", "Source"),
                   col("recorded_by", "Recorded by"), col("approved_by", "Approved by")]
        return table(columns, rows, notes=["Approved expenses only (voided and rejected are left out). Includes salaries, advances and purchases booked as expenses."])

    # ============================================================ PAYROLL
    async def payroll_register(self, params: dict, user) -> dict:
        from app.payroll.models import LineKind, PayrollRun, PayrollStatus, Payslip, PayslipLine

        month = _date(params.get("run_month")) or institute_today().replace(day=1)
        run = (await self.session.execute(select(PayrollRun).where(PayrollRun.month == month, PayrollRun.status != PayrollStatus.CANCELLED))).scalars().first()
        if run is None:
            raise ValidationAppError(f"No payroll has been prepared for {month:%B %Y}.")
        slips = list((await self.session.execute(select(Payslip).where(Payslip.run_id == run.id).order_by(Payslip.employee_name))).scalars())
        lines = (await self.session.execute(select(PayslipLine).where(PayslipLine.payslip_id.in_([s.id for s in slips])))).scalars().all() if slips else []

        def head(ln) -> str:
            if ln.kind == LineKind.EARNING:
                return {"BASE": "Basic", "BONUS": "Bonus", "ADJUSTMENT": "Adjustment +"}.get(ln.source, "Allowances")
            return {"ABSENT": "Absence", "LATE": "Late marks", "UNPAID": "Unpaid leave", "ADVANCE": "Advance recovery", "SECURITY": "Security deposit",
                    "ADJUSTMENT": "Adjustment −"}.get(ln.source, ln.label.split("(")[0].strip()[:30] or "Deduction")

        by_slip: dict = {}
        earn_heads, ded_heads = [], []
        for ln in lines:
            h = head(ln)
            (earn_heads if ln.kind == LineKind.EARNING else ded_heads).append(h) if h not in (earn_heads + ded_heads) else None
            d = by_slip.setdefault(ln.payslip_id, {})
            d[h] = round(d.get(h, 0.0) + _f(ln.amount), 2)
        earn_heads.sort(key=lambda h: (h != "Basic", h))
        ded_heads.sort()
        rows = []
        for s in slips:
            d = by_slip.get(s.id, {})
            rows.append({"employee": s.employee_name, "code": s.employee_code, "department": s.department or "", "job": s.job_title or "",
                         **{f"e:{h}": d.get(h, 0.0) for h in earn_heads}, "gross": _f(s.gross), **{f"d:{h}": d.get(h, 0.0) for h in ded_heads},
                         "deductions": _f(s.deductions), "net": _f(s.net), "held": "Held" if s.held else ""})
        columns = [col("employee", "Employee"), col("code", "Code"), col("department", "Department"), col("job", "Job")] + \
                  [col(f"e:{h}", h, "money", True) for h in earn_heads] + [col("gross", "Gross", "money", True)] + \
                  [col(f"d:{h}", h, "money", True) for h in ded_heads] + [col("deductions", "Total deductions", "money", True), col("net", "Net pay", "money", True), col("held", "Held")]
        return table(columns, rows, subtitle=f"{run.month:%B %Y} · {run.status.value.title()}" + (f" · paid {run.paid_date}" if run.paid_date else ""),
                     notes=["Held payslips are listed but are not part of the month's payment until released."])

    async def advances_report(self, params: dict, user) -> dict:
        from app.payroll.service import PayrollService

        status = params.get("status") or None
        from app.payroll.models import AdvanceStatus

        rows = await PayrollService(self.session).advances(None, AdvanceStatus(status) if status else None)
        out = [{"employee": r["employee_name"], "code": r["employee_code"], "date": r["granted_on"], "amount": r["amount"], "monthly": r["monthly_recovery"],
                "recovered": round(r["amount"] - r["balance"], 2) if str(r["status"]).split(".")[-1] in ("ACTIVE", "CLEARED") else 0.0,
                "balance": r["balance"] if str(r["status"]).split(".")[-1] in ("ACTIVE", "CLEARED") else 0.0,
                "status": str(r["status"]).split(".")[-1].title(), "reason": r["reason"]} for r in rows]
        columns = [col("employee", "Employee"), col("code", "Code"), col("date", "Date", "date"), col("amount", "Advance", "money", True),
                   col("monthly", "Each month", "money"), col("recovered", "Recovered", "money", True), col("balance", "Still owed", "money", True),
                   col("status", "Status"), col("reason", "Reason")]
        return table(columns, out)

    async def annual_deductions(self, params: dict, user) -> dict:
        from app.payroll.models import LineKind, PayrollRun, PayrollStatus, Payslip, PayslipLine

        year = _int(params.get("year")) or institute_today().year
        rows = (await self.session.execute(
            select(Payslip.employee_id, Payslip.employee_name, Payslip.employee_code, PayslipLine.source, PayslipLine.label, func.sum(PayslipLine.amount))
            .join(PayslipLine, PayslipLine.payslip_id == Payslip.id).join(PayrollRun, PayrollRun.id == Payslip.run_id)
            .where(PayrollRun.status == PayrollStatus.PAID, Payslip.held.is_(False), PayslipLine.kind == LineKind.DEDUCTION,
                   PayrollRun.month >= date(year, 1, 1), PayrollRun.month <= date(year, 12, 31))
            .group_by(Payslip.employee_id, Payslip.employee_name, Payslip.employee_code, PayslipLine.source, PayslipLine.label))).all()
        gross = dict((await self.session.execute(
            select(Payslip.employee_id, func.sum(Payslip.gross)).join(PayrollRun, PayrollRun.id == Payslip.run_id)
            .where(PayrollRun.status == PayrollStatus.PAID, Payslip.held.is_(False), PayrollRun.month >= date(year, 1, 1), PayrollRun.month <= date(year, 12, 31))
            .group_by(Payslip.employee_id))).all())
        heads, people = [], {}
        for emp, name, code, source, label, amt in rows:
            h = {"SECURITY": "Security deposit", "ADVANCE": "Advance recovery", "UNPAID": "Unpaid leave", "ABSENT": "Absence", "LATE": "Late marks"}.get(source) \
                or (label or "Deduction").split(":")[0].split("(")[0].strip()[:40]
            if h not in heads:
                heads.append(h)
            p = people.setdefault(emp, {"employee": name, "code": code, "gross": _f(gross.get(emp))})
            p[f"h:{h}"] = round(p.get(f"h:{h}", 0.0) + _f(amt), 2)
        heads.sort()
        out = []
        for p in sorted(people.values(), key=lambda x: x["employee"]):
            for h in heads:
                p.setdefault(f"h:{h}", 0.0)
            p["total"] = round(sum(p[f"h:{h}"] for h in heads), 2)
            out.append(p)
        columns = [col("employee", "Employee"), col("code", "Code"), col("gross", "Gross pay in the year", "money", True)] + \
                  [col(f"h:{h}", h, "money", True) for h in heads] + [col("total", "Total deducted", "money", True)]
        return table(columns, out, subtitle=f"Paid payrolls of {year}", notes=["Use the tax and provident fund columns for each employee's yearly deduction certificate."])

    # ============================================================ ACADEMICS
    async def class_strength(self, params: dict, user) -> dict:
        from app.programs.models import Program
        from app.students.models import Student, StudentStatus

        stmt = select(Program.code, Student.current_semester, Student.section_label, Student.status, Student.gender, func.count()).outerjoin(
            Program, Program.id == Student.program_id).group_by(Program.code, Student.current_semester, Student.section_label, Student.status, Student.gender)
        if _uuid(params.get("program_id")):
            stmt = stmt.where(Student.program_id == _uuid(params["program_id"]))
        groups: dict = {}
        for prog, sem, sec, status, gender, n in (await self.session.execute(stmt)).all():
            k = (prog or "", sem or 0, (sec or "").strip().upper())
            g = groups.setdefault(k, {"program": prog or "No program", "semester": sem, "section": (sec or "").strip().upper() or "—",
                                      "active": 0, "male": 0, "female": 0, "suspended": 0, "dropped": 0, "graduated": 0, "total": 0})
            g["total"] += n
            key = status.value.lower()
            if key in g:
                g[key] += n
            if status == StudentStatus.ACTIVE:
                gl = (gender or "").strip().lower()
                if gl.startswith("m"):
                    g["male"] += n
                elif gl.startswith("f"):
                    g["female"] += n
        columns = [col("program", "Program"), col("semester", "Semester", "number"), col("section", "Section"), col("active", "Active", "number", True),
                   col("male", "Male (active)", "number", True), col("female", "Female (active)", "number", True), col("suspended", "Suspended", "number", True),
                   col("dropped", "Dropped", "number", True), col("graduated", "Graduated", "number", True), col("total", "All records", "number", True)]
        return table(columns, [groups[k] for k in sorted(groups)])

    async def _offerings(self, params: dict, user):
        from app.academics.models import Course, CourseOffering
        from app.employees.models import Employee

        stmt = select(CourseOffering, Course, Employee).join(Course, Course.id == CourseOffering.course_id).outerjoin(Employee, Employee.id == CourseOffering.assigned_teacher_id)
        if params.get("term"):
            stmt = stmt.where(CourseOffering.term_label == params["term"])
        limited = await self._hod_teacher_ids(user)
        if limited is not None:
            stmt = stmt.where(CourseOffering.assigned_teacher_id.in_(limited or [uuid.uuid4()]))
        return (await self.session.execute(stmt.order_by(Course.code, CourseOffering.section_label))).all()

    async def attendance_shortage(self, params: dict, user) -> dict:
        from app.academics.models import ClassSession, Enrollment, EnrollmentStatus
        from app.attendance.models import AttendanceRecord, AttendanceStatus
        from app.programs.models import Program
        from app.students.models import Student

        threshold = float(params.get("below") or settings.attendance_eligibility_threshold_percent)
        offerings = await self._offerings(params, user)
        if not offerings:
            return table([], [], notes=["No classes match."])
        by_id = {o.id: (o, c, t) for o, c, t in offerings}
        counts = (await self.session.execute(
            select(AttendanceRecord.student_id, ClassSession.course_offering_id,
                   func.count().filter(AttendanceRecord.status == AttendanceStatus.PRESENT),
                   func.count().filter(AttendanceRecord.status == AttendanceStatus.LEAVE), func.count())
            .join(ClassSession, ClassSession.id == AttendanceRecord.class_session_id)
            .where(ClassSession.course_offering_id.in_(list(by_id)))
            .group_by(AttendanceRecord.student_id, ClassSession.course_offering_id))).all()
        stmt = select(Enrollment, Student, Program.code).join(Student, Student.id == Enrollment.student_id).outerjoin(Program, Program.id == Student.program_id).where(
            Enrollment.course_offering_id.in_(list(by_id)), Enrollment.status.in_((EnrollmentStatus.ENROLLED, EnrollmentStatus.COMPLETED)))
        if _uuid(params.get("program_id")):
            stmt = stmt.where(Student.program_id == _uuid(params["program_id"]))
        enrolled = {(e.student_id, e.course_offering_id): (st, prog) for e, st, prog in (await self.session.execute(stmt)).all()}
        rows = []
        for sid, oid, present, leave, total in counts:
            if (sid, oid) not in enrolled:
                continue
            held = total - leave
            if held <= 0:
                continue
            pct = round(100 * present / held, 1)
            if pct >= threshold:
                continue
            st, prog = enrolled[(sid, oid)]
            o, c, t = by_id[oid]
            need = 0
            while held + need > 0 and 100 * (present + need) / (held + need) < threshold and need < 500:
                need += 1
            rows.append({"student": f"{st.first_name} {st.last_name}", "roll_number": st.roll_number or "", "program": f"{prog or ''} {st.current_semester or ''}".strip(),
                         "course": f"{c.code} — {c.title}", "section": o.section_label, "teacher": f"{t.first_name} {t.last_name}" if t else "",
                         "held": held, "present": present, "percent": pct, "need": need,
                         "status": "Fine / drop range" if pct <= settings.attendance_fine_threshold_percent else "Below eligibility"})
        rows.sort(key=lambda r: (r["percent"], r["student"]))
        columns = [col("student", "Student"), col("roll_number", "Roll no."), col("program", "Program / sem."), col("course", "Course"), col("section", "Section"),
                   col("teacher", "Teacher"), col("held", "Classes held", "number"), col("present", "Present", "number"), col("percent", "Attendance %", "percent"),
                   col("need", "Classes in a row needed", "number"), col("status", "Status")]
        return table(columns, rows, notes=[f"Students below {threshold:g}% in a course (approved leave is not counted against them).",
                                           "“Classes in a row needed”: how many more classes they must attend, without missing one, to reach the minimum."])

    async def result_analysis(self, params: dict, user) -> dict:
        from app.academics.models import Enrollment, EnrollmentResult, EnrollmentStatus, ResultSheet

        offerings = await self._offerings(params, user)
        if not offerings:
            return table([], [], notes=["No classes match."])
        ids = [o.id for o, _c, _t in offerings]
        enrolled = dict((await self.session.execute(select(Enrollment.course_offering_id, func.count()).where(
            Enrollment.course_offering_id.in_(ids), Enrollment.status != EnrollmentStatus.WITHDRAWN).group_by(Enrollment.course_offering_id))).all())
        res = (await self.session.execute(select(Enrollment.course_offering_id, EnrollmentResult).join(EnrollmentResult, EnrollmentResult.enrollment_id == Enrollment.id)
                                          .where(Enrollment.course_offering_id.in_(ids)))).all()
        sheets = {s.course_offering_id: s.status.value for s in (await self.session.execute(select(ResultSheet).where(ResultSheet.course_offering_id.in_(ids)))).scalars()}
        grades, per = [], {}
        for oid, r in res:
            p = per.setdefault(oid, {"n": 0, "pass": 0, "fail": 0, "att": 0, "pct": 0.0, "gp": 0.0, "g": {}})
            p["n"] += 1
            p["pass" if r.outcome == "PASSED" else "fail"] += 1
            p["att"] += 1 if r.failed_for_attendance else 0
            p["pct"] += float(r.percentage)
            p["gp"] += float(r.grade_points)
            p["g"][r.letter_grade] = p["g"].get(r.letter_grade, 0) + 1
            if r.letter_grade not in grades:
                grades.append(r.letter_grade)
        grades.sort(key=lambda g: (g[0], g))
        rows = []
        for o, c, t in offerings:
            p = per.get(o.id, {"n": 0, "pass": 0, "fail": 0, "att": 0, "pct": 0.0, "gp": 0.0, "g": {}})
            rows.append({"course": f"{c.code} — {c.title}", "term": o.term_label, "section": o.section_label, "teacher": f"{t.first_name} {t.last_name}" if t else "",
                         "enrolled": enrolled.get(o.id, 0), "results": p["n"], "passed": p["pass"], "failed": p["fail"], "failed_attendance": p["att"],
                         "pass_pct": round(100 * p["pass"] / p["n"], 1) if p["n"] else None, "avg_pct": round(p["pct"] / p["n"], 1) if p["n"] else None,
                         "avg_gp": round(p["gp"] / p["n"], 2) if p["n"] else None, **{f"g:{g}": p["g"].get(g, 0) for g in grades},
                         "sheet": (sheets.get(o.id) or "not started").replace("_", " ").title()})
        columns = [col("course", "Course"), col("term", "Term"), col("section", "Section"), col("teacher", "Teacher"), col("enrolled", "Enrolled", "number", True),
                   col("results", "Results given", "number", True), col("passed", "Passed", "number", True), col("failed", "Failed", "number", True),
                   col("failed_attendance", "Failed for attendance", "number", True), col("pass_pct", "Pass %", "percent"), col("avg_pct", "Average %", "percent"),
                   col("avg_gp", "Average grade points", "number")] + [col(f"g:{g}", g, "number", True) for g in grades] + [col("sheet", "Result sheet")]
        out = table(columns, rows, notes=["Published results only (frozen at publication). Supplementary passes are included as passes."])
        if out["totals"].get("results"):
            out["totals"]["pass_pct"] = round(100 * out["totals"]["passed"] / out["totals"]["results"], 1)
        return out

    # ============================================================ STAFF
    async def staff_attendance(self, params: dict, user) -> dict:
        from app.timekeeping.service import TimekeepingService

        month = _month(params)
        rows = await TimekeepingService(self.session).month_report(month)
        out = [{"name": r["name"], "code": r["employee_code"], "job": r["job_title"], "scheduled": r.get("scheduled", 0), "present": r.get("present", 0),
                "excused": r.get("excused", 0), "absent": len(r.get("absent_dates") or []), "late": r.get("late", 0), "early": r.get("early", 0),
                "missing_out": r.get("missing_out", 0)} for r in rows]
        columns = [col("name", "Employee"), col("code", "Code"), col("job", "Job"), col("scheduled", "Working days", "number"), col("present", "Present", "number", True),
                   col("excused", "Excused / leave", "number", True), col("absent", "Absent", "number", True), col("late", "Late", "number", True),
                   col("early", "Left early", "number", True), col("missing_out", "No leaving mark", "number", True)]
        return table(columns, out, subtitle=month.strftime("%B %Y"))

    async def joiners_leavers(self, params: dict, user) -> dict:
        from app.employees.models import Employee, JobRole

        frm, to = _period(params, 365)
        rows = (await self.session.execute(select(Employee, JobRole.title).join(JobRole, JobRole.id == Employee.job_role_id).where(
            or_(and_(Employee.hire_date >= frm, Employee.hire_date <= to), and_(Employee.left_on.is_not(None), Employee.left_on >= frm, Employee.left_on <= to))))).all()
        out = []
        for e, title in rows:
            if frm <= e.hire_date <= to:
                out.append({"event": "Joined", "date": e.hire_date, "name": f"{e.first_name} {e.last_name}", "code": e.employee_code, "job": title,
                            "department": e.department or "", "status": e.status.value.replace("_", " ").title()})
            if e.left_on and frm <= e.left_on <= to:
                out.append({"event": "Left", "date": e.left_on, "name": f"{e.first_name} {e.last_name}", "code": e.employee_code, "job": title,
                            "department": e.department or "", "status": e.status.value.replace("_", " ").title()})
        out.sort(key=lambda r: (r["date"], r["event"]))
        columns = [col("date", "Date", "date"), col("event", "Joined / left"), col("name", "Employee"), col("code", "Code"), col("job", "Job"),
                   col("department", "Department"), col("status", "Status now")]
        joined, left = sum(1 for r in out if r["event"] == "Joined"), sum(1 for r in out if r["event"] == "Left")
        return table(columns, out, subtitle=f"{joined} joined, {left} left")

    # ============================================================ STORE
    async def stock_valuation(self, params: dict, user) -> dict:
        from app.inventory.models import StockItem

        stmt = select(StockItem).where(StockItem.is_active.is_(True))
        if params.get("category"):
            stmt = stmt.where(func.lower(StockItem.category) == params["category"].strip().lower())
        items = list((await self.session.execute(stmt.order_by(StockItem.category, StockItem.name))).scalars())
        rows = [{"code": i.code, "name": i.name, "category": i.category, "unit": i.unit, "qty": _f(i.quantity_on_hand), "avg_cost": _f(i.average_cost),
                 "value": round(_f(i.quantity_on_hand) * _f(i.average_cost), 2), "reorder": _f(i.reorder_level),
                 "low": "Re-order" if _f(i.reorder_level) > 0 and _f(i.quantity_on_hand) <= _f(i.reorder_level) else ""} for i in items]
        if params.get("low_only") in ("1", "true", True):
            rows = [r for r in rows if r["low"]]
        columns = [col("code", "Code"), col("name", "Item"), col("category", "Category"), col("unit", "Unit"), col("qty", "In stock", "number"),
                   col("avg_cost", "Average cost", "money"), col("value", "Value", "money", True), col("reorder", "Re-order level", "number"), col("low", "")]
        return table(columns, rows, notes=["Valued at moving average cost."])

    async def stock_ledger(self, params: dict, user) -> dict:
        from app.inventory.models import MovementKind, StockItem, StockMovement

        item = await self.session.get(StockItem, _uuid(params.get("item_id")))
        if item is None:
            raise ResourceNotFoundError("Item not found.")
        frm, to = _period(params, 365)
        day = func.date(func.timezone(settings.institute_timezone, StockMovement.created_at))
        before = (await self.session.execute(select(StockMovement.balance_after).where(StockMovement.item_id == item.id, day < frm)
                                             .order_by(StockMovement.serial_no.desc()).limit(1))).scalar_one_or_none()
        moves = list((await self.session.execute(select(StockMovement).where(StockMovement.item_id == item.id, day >= frm, day <= to).order_by(StockMovement.serial_no))).scalars())
        names = await self._names({m.recorded_by for m in moves})
        rows = [{"date": local_date(m.created_at), "ref": f"MOV-{m.serial_no:06d}", "kind": m.kind.value.title(),
                 "in": _f(m.quantity) if _f(m.quantity) > 0 else 0.0, "out": -_f(m.quantity) if _f(m.quantity) < 0 else 0.0, "balance": _f(m.balance_after),
                 "unit_cost": _f(m.unit_cost), "party": m.party_name or "", "reason": m.reason or "", "by": names.get(m.recorded_by, "")} for m in moves]
        columns = [col("date", "Date", "date"), col("ref", "Movement"), col("kind", "Kind"), col("in", "In", "number", True), col("out", "Out", "number", True),
                   col("balance", "Balance", "number"), col("unit_cost", "Unit cost", "money"), col("party", "To / from"), col("reason", "Reason"), col("by", "By")]
        out = table(columns, rows, subtitle=f"{item.code} — {item.name} · opening {float(before or 0):g} {item.unit}")
        out["totals"]["balance"] = rows[-1]["balance"] if rows else float(before or 0)
        _ = MovementKind
        return out


    # ============================================================ FIXED ASSETS
    async def _asset_maps(self):
        from app.assets.models import AssetCategory, AssetLocation

        cats = {c.id: c for c in (await self.session.execute(select(AssetCategory))).scalars()}
        locs = {loc.id: loc.name for loc in (await self.session.execute(select(AssetLocation))).scalars()}
        return cats, locs

    async def _depreciation_upto(self, day: date, *, before: bool = False) -> dict:
        """Live depreciation per asset with period_end on or before `day` (strictly before when before=True)."""
        from app.assets.models import DepreciationEntry

        cond = DepreciationEntry.period_end < day if before else DepreciationEntry.period_end <= day
        return {a: Decimal(str(v)) for a, v in (await self.session.execute(
            select(DepreciationEntry.asset_id, func.sum(DepreciationEntry.amount)).where(cond, DepreciationEntry.reversed_at.is_(None))
            .group_by(DepreciationEntry.asset_id))).all()}

    @staticmethod
    def _asset_start(a) -> date:
        """The day the asset came into these books: bought / received, or the go-live date for one owned before the system."""
        return a.depreciation_from if a.source.value == "OPENING" or a.acquired_on is None else min(a.acquired_on, a.depreciation_from)

    async def asset_register(self, params: dict, user) -> dict:
        from app.assets.models import Asset, AssetStatus
        from app.employees.models import Employee

        as_of = _date(params.get("as_of"), institute_today())
        cats, locs = await self._asset_maps()
        stmt = select(Asset).order_by(Asset.tag)
        if _uuid(params.get("category_id")):
            stmt = stmt.where(Asset.category_id == _uuid(params["category_id"]))
        if _uuid(params.get("location_id")):
            stmt = stmt.where(Asset.location_id == _uuid(params["location_id"]))
        if params.get("status"):
            stmt = stmt.where(Asset.status == AssetStatus(params["status"]))
        assets = [a for a in (await self.session.execute(stmt)).scalars()
                  if self._asset_start(a) <= as_of and (a.disposed_on is None or a.disposed_on > as_of)]
        dep = await self._depreciation_upto(as_of)
        emp_ids = {a.custodian_employee_id for a in assets if a.custodian_employee_id}
        emps = {e.id: e for e in (await self.session.execute(select(Employee).where(Employee.id.in_(emp_ids)))).scalars()} if emp_ids else {}
        rows = []
        for a in assets:
            acc = Decimal(str(a.opening_accumulated)) + dep.get(a.id, Decimal(0))
            e = emps.get(a.custodian_employee_id)
            rows.append({"tag": a.tag, "name": a.name, "category": cats[a.category_id].name, "location": locs.get(a.location_id, ""),
                         "custodian": e.full_name if e else "", "status": a.status.value.replace("_", " ").title(), "condition": a.condition.value.title(),
                         "acquired": a.acquired_on, "cost": _f(a.cost) , "estimate": "estimated" if a.cost_is_estimate else "",
                         "accumulated": _f(acc), "book_value": _f(Decimal(str(a.cost)) - acc)})
        columns = [col("tag", "Tag"), col("name", "Asset"), col("category", "Category"), col("location", "Place"), col("custodian", "Held by"),
                   col("status", "Status"), col("condition", "Condition"), col("acquired", "Bought", "date"), col("cost", "Cost", "money", True),
                   col("estimate", "Cost basis"), col("accumulated", "Depreciation to date", "money", True), col("book_value", "Book value", "money", True)]
        return table(columns, rows, subtitle=f"As on {as_of:%d %b %Y} · {len(rows)} assets",
                     notes=["Book value = cost - depreciation used up before the system - depreciation posted up to this date.",
                            "Depreciation is counted only for months that have been posted (Fixed assets > Depreciation)."])

    async def asset_schedule(self, params: dict, user) -> dict:
        """The fixed-asset schedule for a financial year: opening book value + additions - depreciation - disposals = closing."""
        from app.assets.models import Asset, AssetDisposal

        first, last, _months = self._fy(params)
        cats, _locs = await self._asset_maps()
        by_asset = params.get("detail") in ("1", "true")
        dep_before = await self._depreciation_upto(first, before=True)
        dep_to_end = await self._depreciation_upto(last)
        sold = {d.asset_id: d for d in (await self.session.execute(select(AssetDisposal).where(AssetDisposal.status == "APPROVED"))).scalars()}
        lines: dict = {}
        for a in (await self.session.execute(select(Asset).order_by(Asset.tag))).scalars():
            start = self._asset_start(a)
            if start > last or (a.disposed_on is not None and a.disposed_on < first):
                continue
            entry_value = Decimal(str(a.cost)) - Decimal(str(a.opening_accumulated))
            opening = entry_value - dep_before.get(a.id, Decimal(0)) if start < first else Decimal(0)
            added = entry_value if start >= first and a.source.value != "OPENING" else Decimal(0)
            brought = entry_value if start >= first and a.source.value == "OPENING" else Decimal(0)
            year_dep = dep_to_end.get(a.id, Decimal(0)) - dep_before.get(a.id, Decimal(0))
            disposed = Decimal(0)
            if a.disposed_on is not None and first <= a.disposed_on <= last and a.id in sold:
                disposed = Decimal(str(sold[a.id].book_value or 0))
            key = (a.tag, a.name) if by_asset else (cats[a.category_id].name, "")
            r = lines.setdefault(key, {"line": key[0], "asset": key[1], "category": cats[a.category_id].name, "count": 0, "opening": Decimal(0), "added": Decimal(0),
                                       "brought_in": Decimal(0), "depreciation": Decimal(0), "disposals": Decimal(0)})
            r["count"] += 1
            for k, v in (("opening", opening), ("added", added), ("brought_in", brought), ("depreciation", year_dep), ("disposals", disposed)):
                r[k] += v
        rows = []
        for r in sorted(lines.values(), key=lambda x: (x["category"], x["line"])):
            r["closing"] = r["opening"] + r["added"] + r["brought_in"] - r["depreciation"] - r["disposals"]
            rows.append({k: (_f(v) if isinstance(v, Decimal) else v) for k, v in r.items()})
        columns = [col("line", "Asset" if by_asset else "Category")] + ([col("asset", "Name"), col("category", "Category")] if by_asset else [col("count", "Assets", "number", True)]) + [
            col("opening", f"Book value {first:%d %b %Y}", "money", True), col("added", "Bought in the year", "money", True),
            col("brought_in", "Brought in (owned before the system)", "money", True), col("depreciation", "Depreciation for the year", "money", True),
            col("disposals", "Disposed of (book value)", "money", True), col("closing", f"Book value {last:%d %b %Y}", "money", True)]
        return table(columns, rows, subtitle=f"Financial year {first:%b %Y} – {last:%b %Y}",
                     notes=["Opening + bought + brought in - depreciation - disposed of = closing.",
                            "An asset owned before the system enters at its book value on the go-live date (cost less what was already used up)."])

    async def asset_verification(self, params: dict, user) -> dict:
        from app.assets.service import AssetService

        d = await AssetService(self.session).check_detail(_uuid(params.get("check_id")))
        label = {"FOUND": "Found", "WRONG_PLACE": "Found in another place", "MISSING": "Missing", "NOT_CHECKED": "Not checked yet"}
        rows = [{"tag": x["tag"], "name": x["name"], "expected": x["expected_location"] or "", "result": label.get(x["result"], x["result"]),
                 "found": x["found_location"] or "", "condition": (x["condition"] or "").title(), "note": x["note"] or "",
                 "listed": "" if x["expected"] else "not on the list"} for x in d["lines"]]
        order = {"Missing": 0, "Found in another place": 1, "Not checked yet": 2, "Found": 3}
        rows.sort(key=lambda r: (order.get(r["result"], 9), r["tag"]))
        counts = ", ".join(f"{label.get(k, k)}: {v}" for k, v in sorted(d["counts"].items()))
        columns = [col("tag", "Tag"), col("name", "Asset"), col("expected", "Should be in"), col("result", "Result"), col("found", "Found in"),
                   col("condition", "Condition"), col("listed", ""), col("note", "Note")]
        return table(columns, rows, totals={}, subtitle=f"{d['title']} · {d['location']} · {d['status'].lower()} · {counts}")

    async def asset_disposals(self, params: dict, user) -> dict:
        from app.assets.models import Asset, AssetDisposal

        frm, to = _period(params, 365)
        cats, _locs = await self._asset_maps()
        rows_db = (await self.session.execute(select(AssetDisposal, Asset).join(Asset, Asset.id == AssetDisposal.asset_id)
                                              .where(AssetDisposal.status == "APPROVED", AssetDisposal.disposed_on >= frm, AssetDisposal.disposed_on <= to)
                                              .order_by(AssetDisposal.disposed_on))).all()
        names = await self._names({d.decided_by for d, _a in rows_db} | {d.requested_by for d, _a in rows_db})
        rows = [{"date": d.disposed_on, "tag": a.tag, "name": a.name, "category": cats[a.category_id].name, "method": d.method.value.replace("_", " ").title(),
                 "cost": _f(a.cost), "book_value": _f(d.book_value), "proceeds": _f(d.proceeds), "gain": _f(Decimal(str(d.proceeds)) - Decimal(str(d.book_value or 0))),
                 "reason": d.reason, "asked": names.get(d.requested_by, ""), "approved": names.get(d.decided_by, "")} for d, a in rows_db]
        columns = [col("date", "Date", "date"), col("tag", "Tag"), col("name", "Asset"), col("category", "Category"), col("method", "How"),
                   col("cost", "Cost", "money", True), col("book_value", "Book value", "money", True), col("proceeds", "Received", "money", True),
                   col("gain", "Gain / (loss)", "money", True), col("reason", "Reason"), col("asked", "Asked by"), col("approved", "Approved by")]
        return table(columns, rows, notes=["Money received is recorded as income ('Sale of fixed assets') in Accounts on the disposal date."])

    async def asset_maintenance(self, params: dict, user) -> dict:
        from app.assets.models import Asset, AssetEvent

        frm, to = _period(params, 365)
        ev = (await self.session.execute(select(AssetEvent, Asset).join(Asset, Asset.id == AssetEvent.asset_id)
                                         .where(AssetEvent.kind == "REPAIR", AssetEvent.happened_on >= frm, AssetEvent.happened_on <= to)
                                         .order_by(AssetEvent.happened_on))).all()
        names = await self._names({e.recorded_by for e, _a in ev})
        rows = [{"date": e.happened_on, "tag": a.tag, "name": a.name, "work": e.summary, "vendor": e.vendor_name or "", "cost": _f(e.cost),
                 "note": e.note or "", "by": names.get(e.recorded_by, "")} for e, a in ev]
        columns = [col("date", "Date", "date"), col("tag", "Tag"), col("name", "Asset"), col("work", "Work done"), col("vendor", "Done by"),
                   col("cost", "Cost", "money", True), col("note", "Note"), col("by", "Recorded by")]
        return table(columns, rows, notes=["Repair costs are paid through Expenses; this log shows which asset they were for."])

# ============================================================== the catalogue
FEE_STAFF = (P.ACCOUNT_VIEW, P.FEE_MANAGE)
OVERVIEW = (P.INSTITUTE_OVERVIEW,)
ACADEMIC = (P.ACADEMIC_OVERSIGHT, P.RESULT_APPROVE, P.INSTITUTE_OVERVIEW)
ASSETS = (P.ASSET_VIEW,)
F_ASSET_CATEGORY = {"key": "category_id", "label": "Category", "type": "asset_category"}
F_ASSET_LOCATION = {"key": "location_id", "label": "Place", "type": "asset_location"}

REPORTS: dict[str, Report] = {r.key: r for r in [
    Report("fees.position", "Fees", "Fee position", "Billed, discounts, paid, still owed and overdue, by program and semester, fee head, term or fee.",
           FEE_STAFF + OVERVIEW, [F_TERM, F_PROGRAM, F_SEMESTER, {"key": "group_by", "label": "Group by", "type": "select", "default": "program",
                                  "options": [{"value": "program", "label": "Program and semester"}, {"value": "fee_head", "label": "Fee head"},
                                              {"value": "term", "label": "Term"}, {"value": "structure", "label": "Fee"}]}],
           lambda c, p, u: c.fee_position(p, u)),
    Report("fees.statement", "Fees", "Student account statement", "One student's fee ledger: every charge, discount, payment, reversal and refund with a running balance. Print it for the student or parent.",
           FEE_STAFF, [F_STUDENT], lambda c, p, u: c.student_statement(p, u), needs=("student_id",)),
    Report("fees.scholarships", "Fees", "Scholarships and discounts", "Who has a scholarship or discount, how much, and what it has cost so far.",
           FEE_STAFF + OVERVIEW, [{"key": "status", "label": "Status", "type": "select", "options": [{"value": "", "label": "All"}, {"value": "ACTIVE", "label": "Running"}, {"value": "ENDED", "label": "Ended"}]},
                                  {"key": "from_date", "label": "Granted from", "type": "date"}, {"key": "to_date", "label": "Granted to", "type": "date"}],
           lambda c, p, u: c.scholarships(p, u)),
    Report("fees.previous_dues", "Fees", "Previous dues (arrears)", "Dues brought in from the manual system: what was brought in, paid and still owed.",
           FEE_STAFF, [{"key": "only_unpaid", "label": "Only still owed", "type": "check"}, {"key": "q", "label": "Search", "type": "text"}],
           lambda c, p, u: c.previous_dues(p, u)),
    Report("fees.fines", "Fees", "Fines, late fees and other charges", "Issued, collected, waived and still unpaid, by kind (library, late fee, hostel, attendance, store...).",
           FEE_STAFF + OVERVIEW, f_period(30), lambda c, p, u: c.fines_summary(p, u)),
    Report("accounts.income_statement", "Accounts", "Income and expenditure (financial year)", "Month by month: fee and other income against expenses by category, and the surplus.",
           (P.ACCOUNT_VIEW,) + OVERVIEW, [{"key": "fy_start", "label": "Year starting", "type": "fy"}], lambda c, p, u: c.income_statement(p, u)),
    Report("accounts.cash_book", "Accounts", "Cash book", "Every cash receipt and payment with the running cash balance, or one line per day with opening and closing cash.",
           (P.ACCOUNT_VIEW,), f_period(7) + [{"key": "view", "label": "Show", "type": "select", "default": "detail",
                                              "options": [{"value": "detail", "label": "Every entry"}, {"value": "daily", "label": "One line per day"}]},
                                             {"key": "cashier_id", "label": "Recorded by", "type": "cashier"}],
           lambda c, p, u: c.cash_book(p, u)),
    Report("accounts.expenses", "Accounts", "Expense register", "Approved expenses in a period, or totals by category, vendor or month.",
           (P.ACCOUNT_VIEW,), f_period(30) + [{"key": "category_id", "label": "Category", "type": "expense_category"}, {"key": "vendor_id", "label": "Vendor", "type": "vendor"},
                                              {"key": "group_by", "label": "Show", "type": "select", "default": "",
                                               "options": [{"value": "", "label": "Every expense"}, {"value": "category", "label": "Totals by category"},
                                                           {"value": "vendor", "label": "Totals by vendor"}, {"value": "month", "label": "Totals by month"}]}],
           lambda c, p, u: c.expense_register(p, u)),
    Report("payroll.register", "Payroll", "Payroll register", "One month: every employee's basic, allowances, gross, each deduction and net pay.",
           (P.PAYROLL_VIEW,), [{"key": "run_month", "label": "Month", "type": "payroll_run"}], lambda c, p, u: c.payroll_register(p, u), needs=("run_month",)),
    Report("payroll.advances", "Payroll", "Salary advances", "Every advance: amount, recovered, still owed, status.",
           (P.PAYROLL_VIEW,), [{"key": "status", "label": "Status", "type": "select", "options": [{"value": "", "label": "All"}, {"value": "ACTIVE", "label": "Being recovered"},
                                                                                                  {"value": "REQUESTED", "label": "Waiting for approval"}, {"value": "APPROVED", "label": "Approved, not paid"},
                                                                                                  {"value": "CLEARED", "label": "Cleared"}]}],
           lambda c, p, u: c.advances_report(p, u)),
    Report("payroll.annual_deductions", "Payroll", "Yearly deductions per employee", "Tax, provident fund and every other deduction per employee in a calendar year (for deduction certificates).",
           (P.PAYROLL_VIEW,), [F_YEAR], lambda c, p, u: c.annual_deductions(p, u)),
    Report("academics.class_strength", "Academics", "Class strength", "Students by program, semester and section: active (male / female), suspended, dropped, graduated.",
           ACADEMIC + (P.STUDENT_CREATE,), [F_PROGRAM], lambda c, p, u: c.class_strength(p, u)),
    Report("academics.attendance_shortage", "Academics", "Attendance shortage", "Students below the minimum attendance in a course, and how many classes they still need.",
           ACADEMIC, [F_TERM, F_PROGRAM, {"key": "below", "label": "Below (%)", "type": "number"}], lambda c, p, u: c.attendance_shortage(p, u), needs=("term",)),
    Report("academics.result_analysis", "Academics", "Result analysis", "Per course and section: pass %, average, grade spread and result-sheet status.",
           ACADEMIC, [F_TERM], lambda c, p, u: c.result_analysis(p, u), needs=("term",)),
    Report("staff.attendance", "Staff", "Staff attendance summary", "Each member of staff's month: present, leave, absent, late, early.",
           (P.STAFF_ATTENDANCE_VIEW,), [F_MONTH], lambda c, p, u: c.staff_attendance(p, u)),
    Report("staff.joiners_leavers", "Staff", "Staff joined and left", "Who joined and who left in a period.",
           (P.EMPLOYEE_VIEW,) + OVERVIEW, f_period(365), lambda c, p, u: c.joiners_leavers(p, u)),
    Report("store.valuation", "Store", "Stock on hand and value", "Every item in the store with quantity, average cost and value; items to re-order.",
           (P.INVENTORY_VIEW,), [{"key": "category", "label": "Category", "type": "text"}, {"key": "low_only", "label": "Only to re-order", "type": "check"}],
           lambda c, p, u: c.stock_valuation(p, u)),
    Report("store.ledger", "Store", "Stock ledger (one item)", "Every movement of one item in a period with the running balance.",
           (P.INVENTORY_VIEW,), [{"key": "item_id", "label": "Item", "type": "stock_item"}] + f_period(365), lambda c, p, u: c.stock_ledger(p, u), needs=("item_id",)),
    Report("assets.register", "Fixed assets", "Fixed asset register", "Every asset with its tag, place, holder, cost, depreciation to date and book value, on any date.",
           ASSETS, [F_ASSET_CATEGORY, F_ASSET_LOCATION,
                    {"key": "status", "label": "Status", "type": "select", "options": [{"value": "", "label": "All on the books"}, {"value": "IN_USE", "label": "In use"},
                                                                                       {"value": "IN_STORE", "label": "In store"}, {"value": "UNDER_REPAIR", "label": "Under repair"},
                                                                                       {"value": "MISSING", "label": "Missing"}]},
                    {"key": "as_of", "label": "As on", "type": "date", "default": "today"}],
           lambda c, p, u: c.asset_register(p, u)),
    Report("assets.schedule", "Fixed assets", "Fixed asset schedule (financial year)", "Per category: book value at the start of the year, bought, brought in, depreciation, disposals and closing book value.",
           ASSETS + (P.ACCOUNT_VIEW,), [{"key": "fy_start", "label": "Year starting", "type": "fy"}, {"key": "detail", "label": "One line per asset", "type": "check"}],
           lambda c, p, u: c.asset_schedule(p, u)),
    Report("assets.verification", "Fixed assets", "Physical check result", "One physical check: what was found, found in the wrong place, or missing.",
           ASSETS, [{"key": "check_id", "label": "Check", "type": "asset_check"}], lambda c, p, u: c.asset_verification(p, u), needs=("check_id",)),
    Report("assets.disposals", "Fixed assets", "Assets disposed of", "Sold, scrapped, donated, lost or written off in a period, with book value, money received and gain or loss.",
           ASSETS, f_period(365), lambda c, p, u: c.asset_disposals(p, u)),
    Report("assets.maintenance", "Fixed assets", "Repairs and maintenance", "Every repair recorded against an asset in a period, with its cost.",
           ASSETS, f_period(365), lambda c, p, u: c.asset_maintenance(p, u)),
]}
