from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import AccountCategory, EntryKind, Expense, ExpenseSource, ExpenseStatus, PaymentMethod
from app.accounts.service import NEEDS_REFERENCE, AccountsService, expense_ref
from app.audit.service import AuditService
from app.auth.models import Role, UserRole
from app.auth.permissions import Roles
from app.core.clock import institute_today
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
from app.employees.models import Employee, EmployeeBonus, EmployeeSalaryHistory, EmployeeStatus, JobRole
from app.notifications.models import NotificationCategory
from app.notifications.service import create_notification
from app.payroll import rules
from app.payroll.models import (
    AdvanceStatus,
    EmployeePayItem,
    LineKind,
    PayItemCalc,
    PayItemKind,
    Payslip,
    PayslipLine,
    PayrollRun,
    PayrollStatus,
    SalaryAdvance,
)

ACTIVE = (EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE)
MAX_MONEY = Decimal("10000000")
S = PayrollStatus


def run_ref(r: PayrollRun) -> str:
    return f"PAY-{r.month.year}-{r.month.month:02d}"


def _f(v) -> float:
    return round(float(v or 0), 2)


class PayrollService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)
        self.accounts = AccountsService(session)

    # ------------------------------------------------------------------ small helpers
    async def _employee(self, employee_id: uuid.UUID) -> Employee:
        e = await self.session.get(Employee, employee_id)
        if e is None:
            raise ResourceNotFoundError("Employee not found.")
        return e

    async def _run(self, run_id: uuid.UUID) -> PayrollRun:
        r = await self.session.get(PayrollRun, run_id)
        if r is None:
            raise ResourceNotFoundError("Payroll run not found.")
        return r

    async def _payslips(self, run_id: uuid.UUID) -> list[Payslip]:
        return list((await self.session.execute(select(Payslip).where(Payslip.run_id == run_id).order_by(Payslip.employee_name))).scalars())

    async def _link_for(self, user_id: uuid.UUID | None) -> str:
        if user_id is None:
            return "/dashboard"
        codes = set((await self.session.execute(select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id))).scalars())
        for role, link in ((Roles.ACCOUNTS_OFFICER, "/dashboard/accounts/payslips"), (Roles.TEACHER, "/dashboard/faculty/payslips"), (Roles.LIBRARIAN, "/dashboard/library/payslips"),
                           (Roles.SECURITY_GUARD, "/dashboard/gate/payslips"), (Roles.EXAM_CONTROLLER, "/dashboard/exam/payslips"), (Roles.HOSTEL_WARDEN, "/dashboard/warden/payslips")):
            if role in codes:
                return link
        return "/dashboard"

    async def _salaries(self, employee_ids: list[uuid.UUID], month: date) -> dict[uuid.UUID, Decimal]:
        """The basic salary in force during that month (the latest change that had started by its last day)."""
        out: dict = {}
        if not employee_ids:
            return out
        end, start = rules.month_end(month), rules.month_start(month)
        rows = (await self.session.execute(select(EmployeeSalaryHistory).where(EmployeeSalaryHistory.employee_id.in_(employee_ids), EmployeeSalaryHistory.effective_from <= end)
                                           .order_by(EmployeeSalaryHistory.effective_from))).scalars()
        for r in rows:
            if r.effective_to is None or r.effective_to >= start:
                out[r.employee_id] = Decimal(str(r.base_salary))
        return out

    async def employees(self, q: str | None) -> list[dict]:
        """Current staff with the salary they are on now, for choosing who an item or advance is for."""
        stmt = select(Employee, JobRole.title).join(JobRole, JobRole.id == Employee.job_role_id).where(Employee.status.in_(ACTIVE)).order_by(Employee.first_name, Employee.last_name)
        for term in (q or "").split():
            stmt = stmt.where((Employee.first_name.ilike(f"%{term}%")) | (Employee.last_name.ilike(f"%{term}%")) | (Employee.employee_code.ilike(f"%{term}%")))
        rows = (await self.session.execute(stmt.limit(50))).all()
        salaries = await self._salaries([e.id for e, _ in rows], institute_today())
        return [{"id": e.id, "name": f"{e.first_name} {e.last_name}", "employee_code": e.employee_code, "job_title": t, "base_salary": _f(salaries.get(e.id))} for e, t in rows]

    # ------------------------------------------------------------------ pay items
    async def pay_items(self, employee_id: uuid.UUID | None) -> list[dict]:
        stmt = select(EmployeePayItem, Employee).join(Employee, Employee.id == EmployeePayItem.employee_id).order_by(Employee.first_name, EmployeePayItem.kind, EmployeePayItem.name)
        if employee_id:
            stmt = stmt.where(EmployeePayItem.employee_id == employee_id)
        return [{"id": i.id, "employee_id": e.id, "employee_name": f"{e.first_name} {e.last_name}", "employee_code": e.employee_code, "kind": i.kind, "name": i.name, "calc": i.calc,
                 "value": _f(i.value), "is_active": i.is_active, "valid_from": i.valid_from, "valid_until": i.valid_until} for i, e in (await self.session.execute(stmt.limit(1000))).all()]

    @staticmethod
    def _check_item(calc: PayItemCalc, value, valid_from, valid_until) -> None:
        v = Decimal(str(value))
        if v <= 0 or (calc == PayItemCalc.PERCENT_OF_BASE and v > 100) or (calc == PayItemCalc.FIXED and v > MAX_MONEY):
            raise BusinessRuleViolationError("A percentage must be between 0 and 100, and an amount must be more than 0.")
        if valid_from and valid_until and valid_until < valid_from:
            raise BusinessRuleViolationError("The end date cannot be before the start date.")

    async def add_pay_item(self, data: dict, *, actor_id: uuid.UUID) -> EmployeePayItem:
        await self._employee(data["employee_id"])
        self._check_item(data["calc"], data["value"], data.get("valid_from"), data.get("valid_until"))
        i = EmployeePayItem(**{**data, "name": data["name"].strip()})
        self.session.add(i)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="payroll.pay_item_added", entity_type="employee_pay_item", entity_id=i.id)
        return i

    async def update_pay_item(self, item_id: uuid.UUID, changes: dict, *, actor_id: uuid.UUID) -> EmployeePayItem:
        i = await self.session.get(EmployeePayItem, item_id)
        if i is None:
            raise ResourceNotFoundError("Pay item not found.")
        for k, v in changes.items():
            setattr(i, k, v.strip() if isinstance(v, str) else v)
        self._check_item(i.calc, i.value, i.valid_from, i.valid_until)
        await self.audit.log(actor_user_id=actor_id, action="payroll.pay_item_updated", entity_type="employee_pay_item", entity_id=i.id)
        return i

    # ------------------------------------------------------------------ advances
    async def advances(self, employee_id: uuid.UUID | None, status: AdvanceStatus | None) -> list[dict]:
        stmt = select(SalaryAdvance, Employee).join(Employee, Employee.id == SalaryAdvance.employee_id).order_by(SalaryAdvance.granted_on.desc())
        if employee_id:
            stmt = stmt.where(SalaryAdvance.employee_id == employee_id)
        if status:
            stmt = stmt.where(SalaryAdvance.status == status)
        return [{"id": a.id, "employee_id": e.id, "employee_name": f"{e.first_name} {e.last_name}", "employee_code": e.employee_code, "amount": _f(a.amount), "monthly_recovery": _f(a.monthly_recovery),
                 "balance": _f(a.balance), "status": a.status, "reason": a.reason, "granted_on": a.granted_on} for a, e in (await self.session.execute(stmt.limit(500))).all()]

    async def grant_advance(self, *, actor_id: uuid.UUID, employee_id: uuid.UUID, amount: Decimal, monthly_recovery: Decimal, reason: str, method: PaymentMethod,
                            reference: str | None, granted_on: date) -> SalaryAdvance:
        e = await self._employee(employee_id)
        if e.status not in ACTIVE:
            raise BusinessRuleViolationError("Choose a current member of staff.")
        amount, recovery = rules.q2(amount), rules.q2(monthly_recovery)
        if amount <= 0 or amount > MAX_MONEY or recovery <= 0 or recovery > amount:
            raise BusinessRuleViolationError("The advance must be more than 0, and the monthly recovery must be more than 0 and no more than the advance.")
        if granted_on > institute_today():
            raise BusinessRuleViolationError("The date cannot be in the future.")
        if method in NEEDS_REFERENCE and not (reference or "").strip():
            raise BusinessRuleViolationError("Enter the cheque number or the bank transaction reference.")
        category = await self._salary_category()
        expense = Expense(category_id=category.id, title=f"Salary advance: {e.first_name} {e.last_name}", description=reason.strip(), amount=amount, expense_date=granted_on, payment_method=method,
                          reference_no=(reference or "").strip() or None, status=ExpenseStatus.APPROVED, source=ExpenseSource.PAYROLL, recorded_by=actor_id, decided_by=actor_id, decided_at=datetime.now(timezone.utc))
        self.session.add(expense)
        await self.session.flush()
        a = SalaryAdvance(employee_id=e.id, amount=amount, monthly_recovery=recovery, balance=amount, status=AdvanceStatus.ACTIVE, reason=reason.strip(), granted_by=actor_id, granted_on=granted_on,
                          payment_method=method, expense_id=expense.id)
        self.session.add(a)
        await self.session.flush()
        expense.source_ref_id = a.id
        await self.audit.log(actor_user_id=actor_id, action="payroll.advance_granted", entity_type="salary_advance", entity_id=a.id, metadata={"amount": str(amount)})
        if e.user_id:
            await create_notification(self.session, user_id=e.user_id, category=NotificationCategory.PAYROLL, title=f"Salary advance: Rs. {float(amount):,.0f}",
                                      message=f"It will be taken back at Rs. {float(recovery):,.0f} a month from your salary.", link=await self._link_for(e.user_id))
        return a

    async def _salary_category(self) -> AccountCategory:
        await self.accounts.categories(None, False)                                  # makes sure the standard categories exist
        return (await self.session.execute(select(AccountCategory).where(AccountCategory.kind == EntryKind.EXPENSE, AccountCategory.name == "Salaries and wages"))).scalars().first()

    # ------------------------------------------------------------------ building a payslip
    async def _bonuses_for(self, employee_id: uuid.UUID, month: date, run_id: uuid.UUID) -> list[EmployeeBonus]:
        taken = select(PayslipLine.ref_id).join(Payslip, Payslip.id == PayslipLine.payslip_id).join(PayrollRun, PayrollRun.id == Payslip.run_id).where(
            PayslipLine.source == "BONUS", PayrollRun.status != S.CANCELLED, PayrollRun.id != run_id, PayslipLine.ref_id.is_not(None))
        return list((await self.session.execute(select(EmployeeBonus).where(EmployeeBonus.employee_id == employee_id, EmployeeBonus.awarded_at <= rules.month_end(month), EmployeeBonus.id.not_in(taken))
                                                .order_by(EmployeeBonus.awarded_at))).scalars())

    async def _fill(self, slip: Payslip, emp: Employee, run: PayrollRun, base: Decimal) -> None:
        """(Re)build every line of one payslip from the salary, pay items, bonuses, unpaid days, adjustment and advances."""
        await self.session.execute(PayslipLine.__table__.delete().where(PayslipLine.payslip_id == slip.id))
        month, dim = run.month, rules.days_in(run.month)
        payable = rules.payable_days(emp.hire_date, month)
        slip.base_salary, slip.days_in_month, slip.days_payable = base, dim, payable
        lines: list[tuple[LineKind, str, str, Decimal, uuid.UUID | None]] = []
        earn, ded = [], []
        earn.append((LineKind.EARNING, "BASE", "Basic salary" + (f" ({payable} of {dim} days)" if payable < dim else ""), rules.prorate(base, payable, dim), None))
        items = (await self.session.execute(select(EmployeePayItem).where(EmployeePayItem.employee_id == emp.id, EmployeePayItem.is_active.is_(True)).order_by(EmployeePayItem.name))).scalars().all()
        for it in items:
            if (it.valid_from and it.valid_from > rules.month_end(month)) or (it.valid_until and it.valid_until < rules.month_start(month)):
                continue
            amt = rules.item_amount(it.calc.value, it.value, base)
            if payable < dim:
                amt = rules.prorate(amt, payable, dim) if it.kind == PayItemKind.ALLOWANCE else amt
            label = it.name + (f" ({float(it.value):g}% of basic)" if it.calc == PayItemCalc.PERCENT_OF_BASE else "")
            (earn if it.kind == PayItemKind.ALLOWANCE else ded).append((LineKind.EARNING if it.kind == PayItemKind.ALLOWANCE else LineKind.DEDUCTION, "ALLOWANCE" if it.kind == PayItemKind.ALLOWANCE else "DEDUCTION", label, amt, None))
        for b in await self._bonuses_for(emp.id, month, run.id):
            earn.append((LineKind.EARNING, "BONUS", f"Bonus: {b.reason}", rules.q2(b.amount), b.id))
        adj = rules.q2(slip.adjustment)
        if adj > 0:
            earn.append((LineKind.EARNING, "ADJUSTMENT", slip.adjustment_note or "Adjustment", adj, None))
        if Decimal(str(slip.unpaid_days)) > 0:
            ded.append((LineKind.DEDUCTION, "UNPAID", f"Unpaid leave ({float(slip.unpaid_days):g} day{'s' if float(slip.unpaid_days) != 1 else ''})", rules.unpaid_deduction(base, dim, slip.unpaid_days), None))
        if adj < 0:
            ded.append((LineKind.DEDUCTION, "ADJUSTMENT", slip.adjustment_note or "Adjustment", -adj, None))
        gross = sum((l[3] for l in earn), Decimal(0))
        available = gross - sum((l[3] for l in ded), Decimal(0))
        advances = (await self.session.execute(select(SalaryAdvance).where(SalaryAdvance.employee_id == emp.id, SalaryAdvance.status == AdvanceStatus.ACTIVE).order_by(SalaryAdvance.granted_on))).scalars().all()
        for a in advances:
            rec = rules.advance_recovery(a.monthly_recovery, a.balance, available)
            if rec > 0:
                ded.append((LineKind.DEDUCTION, "ADVANCE", f"Salary advance recovery (Rs. {float(a.balance):,.0f} owed)", rec, a.id))
                available -= rec
        for n, (kind, source, label, amount, ref) in enumerate(earn + ded):
            self.session.add(PayslipLine(payslip_id=slip.id, position=n, kind=kind, source=source, label=label, amount=amount, ref_id=ref))
        slip.gross, slip.deductions = gross, sum((l[3] for l in ded), Decimal(0))
        slip.net = gross - slip.deductions

    async def _totals(self, run: PayrollRun) -> None:
        slips = [s for s in await self._payslips(run.id) if not s.held]
        run.employees_count = len(slips)
        run.total_gross = sum((Decimal(str(s.gross)) for s in slips), Decimal(0))
        run.total_deductions = sum((Decimal(str(s.deductions)) for s in slips), Decimal(0))
        run.total_net = sum((Decimal(str(s.net)) for s in slips), Decimal(0))

    async def _eligible(self, month: date) -> tuple[list[tuple[Employee, str | None]], dict]:
        end = rules.month_end(month)
        rows = (await self.session.execute(select(Employee, JobRole.title).join(JobRole, JobRole.id == Employee.job_role_id).where(Employee.status.in_(ACTIVE), Employee.hire_date <= end).order_by(Employee.first_name, Employee.last_name))).all()
        return [(e, t) for e, t in rows], await self._salaries([e.id for e, _ in rows], month)

    # ------------------------------------------------------------------ the run
    async def prepare(self, month: date, *, actor_id: uuid.UUID) -> tuple[PayrollRun, list[str]]:
        month = rules.month_start(month)
        today = institute_today()
        if month > rules.month_start(today).replace(day=1) and (month.year, month.month) > ((today.year, today.month + 1) if today.month < 12 else (today.year + 1, 1)):
            raise BusinessRuleViolationError("You can prepare payroll up to next month, not further ahead.")
        if (today.year - month.year) * 12 + today.month - month.month > 24:
            raise BusinessRuleViolationError("That month is more than two years ago.")
        if (await self.session.execute(select(func.count()).select_from(PayrollRun).where(PayrollRun.month == month, PayrollRun.status != S.CANCELLED))).scalar_one():
            raise BusinessRuleViolationError("There is already a payroll run for that month. Open it, or cancel it first.")
        people, salaries = await self._eligible(month)
        run = PayrollRun(month=month, status=S.DRAFT, created_by=actor_id)
        self.session.add(run)
        await self.session.flush()
        await self.session.refresh(run)
        skipped = []
        for emp, title in people:
            base = salaries.get(emp.id)
            if base is None or base <= 0:
                skipped.append(f"{emp.first_name} {emp.last_name} (no salary on record)")
                continue
            slip = Payslip(run_id=run.id, employee_id=emp.id, employee_name=f"{emp.first_name} {emp.last_name}", employee_code=emp.employee_code, job_title=title, department=emp.department,
                           base_salary=base, days_in_month=rules.days_in(month), days_payable=rules.payable_days(emp.hire_date, month), gross=0, deductions=0, net=0)
            self.session.add(slip)
            await self.session.flush()
            await self._fill(slip, emp, run, base)
        await self._totals(run)
        if run.employees_count == 0:
            raise BusinessRuleViolationError("There is nobody to pay for that month. Check that employees have a salary on record.")
        await self.audit.log(actor_user_id=actor_id, action="payroll.prepared", entity_type="payroll_run", entity_id=run.id, metadata={"month": str(month), "employees": run.employees_count})
        return run, skipped

    async def recalculate(self, run_id: uuid.UUID, *, actor_id: uuid.UUID) -> PayrollRun:
        run = await self._run(run_id)
        if run.status != S.DRAFT:
            raise BusinessRuleViolationError("Only a draft can be recalculated.")
        people, salaries = await self._eligible(run.month)
        have = {s.employee_id: s for s in await self._payslips(run.id)}
        for emp, title in people:
            base = salaries.get(emp.id)
            if base is None or base <= 0:
                continue
            slip = have.get(emp.id)
            if slip is None:
                slip = Payslip(run_id=run.id, employee_id=emp.id, employee_name=f"{emp.first_name} {emp.last_name}", employee_code=emp.employee_code, job_title=title, department=emp.department,
                               base_salary=base, days_in_month=rules.days_in(run.month), days_payable=rules.payable_days(emp.hire_date, run.month), gross=0, deductions=0, net=0)
                self.session.add(slip)
                await self.session.flush()
            await self._fill(slip, emp, run, base)
        await self.session.flush()
        await self._totals(run)
        await self.audit.log(actor_user_id=actor_id, action="payroll.recalculated", entity_type="payroll_run", entity_id=run.id)
        return run

    async def update_payslip(self, run_id: uuid.UUID, payslip_id: uuid.UUID, changes: dict, *, actor_id: uuid.UUID) -> Payslip:
        run = await self._run(run_id)
        if run.status != S.DRAFT:
            raise BusinessRuleViolationError("A payslip can only be changed while the run is a draft.")
        slip = await self.session.get(Payslip, payslip_id)
        if slip is None or slip.run_id != run.id:
            raise ResourceNotFoundError("Payslip not found.")
        if "unpaid_days" in changes and changes["unpaid_days"] is not None:
            d = Decimal(str(changes["unpaid_days"]))
            if d < 0 or d > slip.days_in_month or (d * 2) % 1 != 0:
                raise BusinessRuleViolationError(f"Unpaid days must be between 0 and {slip.days_in_month}, in whole or half days.")
            slip.unpaid_days = d
        if "adjustment" in changes and changes["adjustment"] is not None:
            a = rules.q2(changes["adjustment"])
            if abs(a) > MAX_MONEY:
                raise BusinessRuleViolationError("That adjustment is too large.")
            slip.adjustment = a
        if "adjustment_note" in changes:
            slip.adjustment_note = (changes["adjustment_note"] or "").strip() or None
        if Decimal(str(slip.adjustment)) != 0 and not slip.adjustment_note:
            raise BusinessRuleViolationError("Say what the adjustment is for, so it shows on the payslip.")
        if "held" in changes and changes["held"] is not None:
            slip.held = bool(changes["held"])
        if "held_reason" in changes:
            slip.held_reason = (changes["held_reason"] or "").strip() or None
        if slip.held and not slip.held_reason:
            raise BusinessRuleViolationError("Say why this payslip is being held back.")
        if not slip.held:
            slip.held_reason = None
        emp = await self._employee(slip.employee_id)
        await self._fill(slip, emp, run, Decimal(str(slip.base_salary)))
        await self.session.flush()
        await self._totals(run)
        await self.audit.log(actor_user_id=actor_id, action="payroll.payslip_adjusted", entity_type="payslip", entity_id=slip.id)
        return slip

    async def approve(self, run_id: uuid.UUID, *, admin_id: uuid.UUID) -> PayrollRun:
        run = await self._run(run_id)
        if run.status != S.DRAFT:
            raise BusinessRuleViolationError("Only a draft can be approved.")
        slips = [s for s in await self._payslips(run.id) if not s.held]
        if not slips:
            raise BusinessRuleViolationError("Every payslip is held back, so there is nothing to approve.")
        negative = [s.employee_name for s in slips if Decimal(str(s.net)) < 0]
        if negative:
            raise BusinessRuleViolationError("These people would be paid less than nothing: " + ", ".join(negative[:5]) + ". Adjust their deductions or hold their payslip.")
        await self._totals(run)
        run.status, run.approved_by, run.approved_at = S.APPROVED, admin_id, datetime.now(timezone.utc)
        await self.audit.log(actor_user_id=admin_id, action="payroll.approved", entity_type="payroll_run", entity_id=run.id, metadata={"net": str(run.total_net)})
        return run

    async def cancel(self, run_id: uuid.UUID, *, actor_id: uuid.UUID, reason: str) -> PayrollRun:
        run = await self._run(run_id)
        if run.status not in (S.DRAFT, S.APPROVED):
            raise BusinessRuleViolationError("Only a payroll run that has not been paid can be cancelled." if run.status == S.PAID else "This run is already cancelled.")
        if len((reason or "").strip()) < 3:
            raise BusinessRuleViolationError("Say why you are cancelling it.")
        run.status, run.cancelled_by, run.cancelled_at, run.cancel_reason = S.CANCELLED, actor_id, datetime.now(timezone.utc), reason.strip()
        await self.audit.log(actor_user_id=actor_id, action="payroll.cancelled", entity_type="payroll_run", entity_id=run.id, metadata={"reason": reason.strip()})
        return run

    async def pay(self, run_id: uuid.UUID, *, actor_id: uuid.UUID, method: PaymentMethod, reference: str | None, paid_date: date) -> PayrollRun:
        run = await self._run(run_id)
        if run.status != S.APPROVED:
            raise BusinessRuleViolationError("Only an approved payroll run can be paid.")
        if method in NEEDS_REFERENCE and not (reference or "").strip():
            raise BusinessRuleViolationError("Enter the cheque number or the bank transaction reference.")
        if paid_date > institute_today():
            raise BusinessRuleViolationError("The payment date cannot be in the future.")
        slips = [s for s in await self._payslips(run.id) if not s.held]
        total = sum((Decimal(str(s.net)) for s in slips), Decimal(0))
        if total <= 0:
            raise BusinessRuleViolationError("The total to pay is nothing.")
        recovered = Decimal(0)
        for s in slips:
            for ln in (await self.session.execute(select(PayslipLine).where(PayslipLine.payslip_id == s.id, PayslipLine.source == "ADVANCE"))).scalars():
                adv = await self.session.get(SalaryAdvance, ln.ref_id)
                if adv is not None:
                    adv.balance = max(Decimal(0), Decimal(str(adv.balance)) - Decimal(str(ln.amount)))
                    if adv.balance <= Decimal("0.005"):
                        adv.status = AdvanceStatus.CLEARED
                    recovered += Decimal(str(ln.amount))
        category = await self._salary_category()
        label = run.month.strftime("%B %Y")
        e = Expense(category_id=category.id, title=f"Salaries: {label}", description=f"{len(slips)} employees. Net pay. Withheld from pay (tax, provident fund, absences, advances): Rs. {float(run.total_deductions):,.2f}; of which advances recovered Rs. {float(recovered):,.2f}.",
                    amount=total, expense_date=paid_date, payment_method=method, reference_no=(reference or "").strip() or None, status=ExpenseStatus.APPROVED, source=ExpenseSource.PAYROLL, source_ref_id=run.id,
                    recorded_by=actor_id, decided_by=run.approved_by, decided_at=run.approved_at, decision_note="Approved as a payroll run.")
        self.session.add(e)
        await self.session.flush()
        run.status, run.paid_by, run.paid_at, run.paid_date, run.payment_method, run.payment_reference, run.expense_id = S.PAID, actor_id, datetime.now(timezone.utc), paid_date, method, (reference or "").strip() or None, e.id
        await self.audit.log(actor_user_id=actor_id, action="payroll.paid", entity_type="payroll_run", entity_id=run.id, metadata={"net": str(total), "expense": str(e.id)})
        for s in slips:
            user_id = (await self.session.get(Employee, s.employee_id)).user_id
            if user_id:
                await create_notification(self.session, user_id=user_id, category=NotificationCategory.PAYROLL, title=f"Your salary slip for {label} is ready", message=f"Net pay: Rs. {float(s.net):,.0f}.", link=await self._link_for(user_id))
        return run

    async def reverse_payment(self, run_id: uuid.UUID, *, admin_id: uuid.UUID, reason: str) -> PayrollRun:
        run = await self._run(run_id)
        if run.status != S.PAID:
            raise BusinessRuleViolationError("Only a paid payroll run can have its payment reversed.")
        for s in [s for s in await self._payslips(run.id) if not s.held]:
            for ln in (await self.session.execute(select(PayslipLine).where(PayslipLine.payslip_id == s.id, PayslipLine.source == "ADVANCE"))).scalars():
                adv = await self.session.get(SalaryAdvance, ln.ref_id)
                if adv is not None:
                    adv.balance = min(Decimal(str(adv.amount)), Decimal(str(adv.balance)) + Decimal(str(ln.amount)))
                    adv.status = AdvanceStatus.ACTIVE
        e = await self.session.get(Expense, run.expense_id) if run.expense_id else None
        if e is not None:
            e.status, e.voided_at, e.void_reason = ExpenseStatus.VOIDED, datetime.now(timezone.utc), f"Payroll payment reversed: {reason.strip()}"
        run.status, run.paid_by, run.paid_at, run.paid_date, run.payment_method, run.payment_reference, run.expense_id = S.APPROVED, None, None, None, None, None, None
        await self.audit.log(actor_user_id=admin_id, action="payroll.payment_reversed", entity_type="payroll_run", entity_id=run.id, metadata={"reason": reason.strip()})
        return run

    # ------------------------------------------------------------------ reading
    @staticmethod
    def _slip_row(s: Payslip) -> dict:
        return {"id": s.id, "employee_id": s.employee_id, "employee_name": s.employee_name, "employee_code": s.employee_code, "job_title": s.job_title, "base_salary": _f(s.base_salary), "days_payable": s.days_payable,
                "days_in_month": s.days_in_month, "unpaid_days": _f(s.unpaid_days), "adjustment": _f(s.adjustment), "adjustment_note": s.adjustment_note, "held": s.held, "held_reason": s.held_reason,
                "gross": _f(s.gross), "deductions": _f(s.deductions), "net": _f(s.net)}

    async def _run_row(self, r: PayrollRun) -> dict:
        names = await self.accounts._names({r.created_by, r.approved_by, r.paid_by})
        exp = await self.session.get(Expense, r.expense_id) if r.expense_id else None
        return {"id": r.id, "reference": run_ref(r), "month": r.month, "month_label": r.month.strftime("%B %Y"), "status": r.status, "employees_count": r.employees_count, "total_gross": _f(r.total_gross),
                "total_deductions": _f(r.total_deductions), "total_net": _f(r.total_net), "created_by_name": names.get(r.created_by), "approved_by_name": names.get(r.approved_by), "approved_at": r.approved_at,
                "paid_by_name": names.get(r.paid_by), "paid_at": r.paid_at, "paid_date": r.paid_date, "payment_method": r.payment_method, "payment_reference": r.payment_reference,
                "expense_reference": expense_ref(exp) if exp else None, "cancel_reason": r.cancel_reason, "created_at": r.created_at}

    async def runs(self) -> list[dict]:
        return [await self._run_row(r) for r in (await self.session.execute(select(PayrollRun).order_by(PayrollRun.month.desc(), PayrollRun.created_at.desc()).limit(120))).scalars()]

    async def run_detail(self, run_id: uuid.UUID) -> dict:
        run = await self._run(run_id)
        slips = await self._payslips(run.id)
        out = await self._run_row(run)
        out["payslips"] = [self._slip_row(s) for s in slips]
        out["held_count"] = sum(1 for s in slips if s.held)
        out["problems"] = [f"{s.employee_name}: pay would be below zero" for s in slips if not s.held and Decimal(str(s.net)) < 0]
        return out

    async def payslip_detail(self, payslip_id: uuid.UUID, *, viewer_user_id: uuid.UUID, can_view_all: bool) -> dict:
        s = await self.session.get(Payslip, payslip_id)
        if s is None:
            raise ResourceNotFoundError("Payslip not found.")
        run = await self._run(s.run_id)
        if not can_view_all:
            emp = await self.session.get(Employee, s.employee_id)
            if emp is None or emp.user_id != viewer_user_id or run.status != S.PAID or s.held:
                raise ResourceNotFoundError("Payslip not found.")
        lines = list((await self.session.execute(select(PayslipLine).where(PayslipLine.payslip_id == s.id).order_by(PayslipLine.position))).scalars())
        row = self._slip_row(s)
        row.update({"month": run.month, "month_label": run.month.strftime("%B %Y"), "run_status": run.status, "paid_date": run.paid_date, "payment_method": run.payment_method,
                    "earnings": [{"label": ln.label, "source": ln.source, "amount": _f(ln.amount)} for ln in lines if ln.kind == LineKind.EARNING],
                    "deduction_lines": [{"label": ln.label, "source": ln.source, "amount": _f(ln.amount)} for ln in lines if ln.kind == LineKind.DEDUCTION], "department": s.department})
        return row

    async def my_payslips(self, user_id: uuid.UUID) -> list[dict]:
        emp = (await self.session.execute(select(Employee).where(Employee.user_id == user_id))).scalars().first()
        if emp is None:
            return []
        rows = (await self.session.execute(select(Payslip, PayrollRun).join(PayrollRun, PayrollRun.id == Payslip.run_id).where(Payslip.employee_id == emp.id, PayrollRun.status == S.PAID, Payslip.held.is_(False))
                                           .order_by(PayrollRun.month.desc()))).all()
        return [{"id": s.id, "month": r.month, "month_label": r.month.strftime("%B %Y"), "gross": _f(s.gross), "deductions": _f(s.deductions), "net": _f(s.net), "paid_date": r.paid_date} for s, r in rows]