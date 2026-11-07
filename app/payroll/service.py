from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import AccountCategory, EntryKind, Expense, ExpenseSource, ExpenseStatus, PaymentMethod
from app.accounts.service import NEEDS_REFERENCE, AccountsService, expense_ref
from app.audit.service import AuditService
from app.auth.models import Role, UserRole
from app.auth.permissions import Roles
from app.core.clock import institute_today
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
from app.employees.models import Employee, EmployeeBonus, EmployeeSalaryHistory, EmployeeStatus, JobRole
from app.notifications.models import NotificationCategory
from app.notifications.service import create_notification
from app.payroll import rules
from app.payroll.models import (
    AdvanceStatus,
    EmployeePayItem,
    EmployeePayrollSetting,
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
                           (Roles.SECURITY_GUARD, "/dashboard/gate/payslips"), (Roles.EXAM_CONTROLLER, "/dashboard/exam/payslips"), (Roles.HOSTEL_WARDEN, "/dashboard/warden/payslips"),
                           (Roles.STAFF, "/dashboard/staff/payslips")):
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
                                           .order_by(EmployeeSalaryHistory.effective_from, EmployeeSalaryHistory.effective_to.is_(None)))).scalars()  # same start: the open (corrected) row wins
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

    @staticmethod
    def _not_own_pay(e: Employee, actor_id: uuid.UUID, what: str) -> None:
        if e.user_id is not None and e.user_id == actor_id:
            raise BusinessRuleViolationError(f"You cannot {what} for yourself. Another member of staff must do it.")

    async def add_pay_item(self, data: dict, *, actor_id: uuid.UUID) -> EmployeePayItem:
        self._not_own_pay(await self._employee(data["employee_id"]), actor_id, "change pay items")
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
        self._not_own_pay(await self._employee(i.employee_id), actor_id, "change pay items")
        for k, v in changes.items():
            setattr(i, k, v.strip() if isinstance(v, str) else v)
        self._check_item(i.calc, i.value, i.valid_from, i.valid_until)
        await self.audit.log(actor_user_id=actor_id, action="payroll.pay_item_updated", entity_type="employee_pay_item", entity_id=i.id)
        return i

    # ------------------------------------------------------------------ advances
    async def advances(self, employee_id: uuid.UUID | None, status: AdvanceStatus | None) -> list[dict]:
        stmt = select(SalaryAdvance, Employee).join(Employee, Employee.id == SalaryAdvance.employee_id).order_by(SalaryAdvance.created_at.desc())
        if employee_id:
            stmt = stmt.where(SalaryAdvance.employee_id == employee_id)
        if status:
            stmt = stmt.where(SalaryAdvance.status == status)
        rows = (await self.session.execute(stmt.limit(500))).all()
        names = await self.accounts._names({u for a, _ in rows for u in (a.requested_by, a.decided_by, a.paid_by, a.granted_by)})
        return [{"id": a.id, "employee_id": e.id, "employee_name": f"{e.first_name} {e.last_name}", "employee_code": e.employee_code, "amount": _f(a.amount),
                 "monthly_recovery": _f(a.monthly_recovery), "balance": _f(a.balance), "status": a.status, "reason": a.reason, "granted_on": a.granted_on,
                 "payment_method": a.payment_method, "payment_reference": a.payment_reference,
                 "requested_by_name": names.get(a.requested_by or a.granted_by), "decided_by_name": names.get(a.decided_by), "decided_at": a.decided_at,
                 "decision_note": a.decision_note, "paid_by_name": names.get(a.paid_by), "paid_at": a.paid_at, "created_at": a.created_at} for a, e in rows]

    async def _advance(self, advance_id: uuid.UUID) -> SalaryAdvance:
        a = (await self.session.execute(select(SalaryAdvance).where(SalaryAdvance.id == advance_id).with_for_update()
                                        .execution_options(populate_existing=True))).scalar_one_or_none()
        if a is None:
            raise ResourceNotFoundError("Advance not found.")
        return a

    async def _check_advance_limit(self, e: Employee, amount: Decimal, *, ignore: uuid.UUID | None = None) -> None:
        """At most `payroll_advance_max_salary_multiple` months of basic salary may be owed in advances at once,
        counting what is still to be recovered and what is waiting for approval or payout."""
        base = (await self._salaries([e.id], institute_today())).get(e.id) or Decimal(0)
        if base <= 0:
            raise BusinessRuleViolationError("This employee has no basic salary on record, so no advance can be given.")
        cap = rules.q2(base * Decimal(str(settings.payroll_advance_max_salary_multiple or 1)))
        open_rows = (await self.session.execute(select(SalaryAdvance).where(
            SalaryAdvance.employee_id == e.id,
            SalaryAdvance.status.in_((AdvanceStatus.REQUESTED, AdvanceStatus.APPROVED, AdvanceStatus.ACTIVE))))).scalars().all()
        owed = sum((Decimal(str(a.balance if a.status == AdvanceStatus.ACTIVE else a.amount)) for a in open_rows if a.id != ignore), Decimal(0))
        if owed + amount > cap:
            raise BusinessRuleViolationError(
                f"Too much: {e.first_name} {e.last_name} may owe at most Rs. {float(cap):,.0f} in advances "
                f"({float(settings.payroll_advance_max_salary_multiple or 1):g} x basic salary). "
                f"Already owed or waiting: Rs. {float(owed):,.0f}; at most Rs. {float(max(cap - owed, Decimal(0))):,.0f} more.")

    async def request_advance(self, *, actor_id: uuid.UUID, employee_id: uuid.UUID, amount: Decimal, monthly_recovery: Decimal, reason: str,
                              method: PaymentMethod, reference: str | None = None, granted_on: date | None = None) -> SalaryAdvance:
        """Step 1: the accounts office asks for an advance. Nothing is paid yet; an administrator must approve it."""
        e = await self._employee(employee_id)
        if e.status not in ACTIVE:
            raise BusinessRuleViolationError("Choose a current member of staff.")
        if e.user_id is not None and e.user_id == actor_id:
            raise BusinessRuleViolationError("You cannot ask for an advance for yourself here. Another member of staff must request it.")
        amount, recovery = rules.q2(amount), rules.q2(monthly_recovery)
        if amount <= 0 or amount > MAX_MONEY or recovery <= 0 or recovery > amount:
            raise BusinessRuleViolationError("The advance must be more than 0, and the monthly recovery must be more than 0 and no more than the advance.")
        if len((reason or "").strip()) < 3:
            raise BusinessRuleViolationError("Give the reason for the advance.")
        await self._check_advance_limit(e, amount)
        a = SalaryAdvance(employee_id=e.id, amount=amount, monthly_recovery=recovery, balance=amount, status=AdvanceStatus.REQUESTED, reason=reason.strip(),
                          granted_by=None, granted_on=granted_on or institute_today(), payment_method=method, requested_by=actor_id)
        self.session.add(a)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="payroll.advance_requested", entity_type="salary_advance", entity_id=a.id, metadata={"amount": str(amount)})
        for admin in await self._approver_ids():
            if admin != actor_id:
                await create_notification(self.session, user_id=admin, category=NotificationCategory.PAYROLL, title=f"Salary advance to approve: Rs. {float(amount):,.0f}",
                                          message=f"{e.first_name} {e.last_name}: {reason.strip()}", link="/dashboard/admin/payroll?open=advances")
        return a

    async def _approver_ids(self) -> list[uuid.UUID]:
        from app.auth.models import Permission, RolePermission, User

        return list(set((await self.session.execute(
            select(User.id).join(UserRole, UserRole.user_id == User.id).join(RolePermission, RolePermission.role_id == UserRole.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id).where(Permission.code == "payroll.approve", User.is_active.is_(True)))).scalars()))

    async def decide_advance(self, advance_id: uuid.UUID, *, admin_id: uuid.UUID, approve: bool, note: str | None) -> SalaryAdvance:
        """Step 2: an administrator approves or rejects. Never their own request, never an advance to themselves."""
        a = await self._advance(advance_id)
        if a.status != AdvanceStatus.REQUESTED:
            raise BusinessRuleViolationError("This advance is not waiting for a decision.")
        e = await self._employee(a.employee_id)
        from app.core.controls import four_eyes, refuse_self

        refuse_self(admin_id, e.user_id, "An advance to yourself must be decided by another administrator.")
        # Whoever asked for it does not approve it; an institute with a single administrator is not left stuck.
        await four_eyes(self.session, admin_id, [a.requested_by], "payroll.approve",
                        "You asked for this advance, so another administrator must decide it.")
        if not approve and len((note or "").strip()) < 3:
            raise BusinessRuleViolationError("Say why the advance is rejected.")
        if approve:
            await self._check_advance_limit(e, Decimal(str(a.amount)), ignore=a.id)
        a.status = AdvanceStatus.APPROVED if approve else AdvanceStatus.REJECTED
        a.decided_by, a.decided_at, a.decision_note = admin_id, datetime.now(timezone.utc), (note or "").strip() or None
        await self.audit.log(actor_user_id=admin_id, action="payroll.advance_approved" if approve else "payroll.advance_rejected", entity_type="salary_advance", entity_id=a.id)
        if a.requested_by and a.requested_by != admin_id:
            await create_notification(self.session, user_id=a.requested_by, category=NotificationCategory.PAYROLL,
                                      title=f"Salary advance {'approved' if approve else 'rejected'}: {e.first_name} {e.last_name}",
                                      message=f"Rs. {float(a.amount):,.0f}" + (" \u2014 it can be paid now." if approve else f" \u2014 {a.decision_note}"),
                                      link="/dashboard/accounts/payroll?open=advances")
        return a

    async def pay_advance(self, advance_id: uuid.UUID, *, actor_id: uuid.UUID, method: PaymentMethod, reference: str | None, paid_on: date) -> SalaryAdvance:
        """Step 3: the accounts office hands the approved advance over. It becomes an expense and is recovered from the next payslips."""
        a = await self._advance(advance_id)
        if a.status != AdvanceStatus.APPROVED:
            raise BusinessRuleViolationError("Only an approved advance can be paid." if a.status == AdvanceStatus.REQUESTED else f"This advance is {a.status.value.lower()}.")
        e = await self._employee(a.employee_id)
        if e.user_id is not None and e.user_id == actor_id:
            raise BusinessRuleViolationError("You cannot pay an advance to yourself. Another member of staff must hand it over.")
        if paid_on > institute_today():
            raise BusinessRuleViolationError("The date cannot be in the future.")
        if method in NEEDS_REFERENCE and not (reference or "").strip():
            raise BusinessRuleViolationError("Enter the cheque number or the bank transaction reference.")
        from app.accounts.period import ensure_open

        await ensure_open(self.session, paid_on, "This advance")
        category = await self._salary_category()
        expense = Expense(category_id=category.id, title=f"Salary advance: {e.first_name} {e.last_name}", description=a.reason, amount=a.amount, expense_date=paid_on,
                          payment_method=method, reference_no=(reference or "").strip() or None, status=ExpenseStatus.APPROVED, source=ExpenseSource.PAYROLL,
                          source_ref_id=a.id, recorded_by=actor_id, decided_by=a.decided_by, decided_at=a.decided_at, decision_note="Approved as a salary advance.")
        self.session.add(expense)
        await self.session.flush()
        a.status, a.expense_id, a.payment_method, a.payment_reference = AdvanceStatus.ACTIVE, expense.id, method, (reference or "").strip() or None
        a.granted_on, a.granted_by, a.paid_by, a.paid_at = paid_on, a.decided_by, actor_id, datetime.now(timezone.utc)
        await self.audit.log(actor_user_id=actor_id, action="payroll.advance_paid", entity_type="salary_advance", entity_id=a.id, metadata={"amount": str(a.amount), "expense": str(expense.id)})
        if e.user_id:
            await create_notification(self.session, user_id=e.user_id, category=NotificationCategory.PAYROLL, title=f"Salary advance: Rs. {float(a.amount):,.0f}",
                                      message=f"It will be taken back at Rs. {float(a.monthly_recovery):,.0f} a month from your salary.", link=await self._link_for(e.user_id))
        return a

    async def cancel_advance(self, advance_id: uuid.UUID, *, actor_id: uuid.UUID, reason: str) -> SalaryAdvance:
        a = await self._advance(advance_id)
        if a.status not in (AdvanceStatus.REQUESTED, AdvanceStatus.APPROVED):
            raise BusinessRuleViolationError("Only an advance that has not been paid can be cancelled.")
        if len((reason or "").strip()) < 3:
            raise BusinessRuleViolationError("Say why it is cancelled.")
        a.status, a.decision_note = AdvanceStatus.CANCELLED, f"Cancelled: {reason.strip()}"[:300]
        await self.audit.log(actor_user_id=actor_id, action="payroll.advance_cancelled", entity_type="salary_advance", entity_id=a.id, metadata={"reason": reason.strip()})
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

    async def _attendance(self, month: date, employees: list[Employee]) -> dict:
        from app.timekeeping.service import TimekeepingService

        return await TimekeepingService(self.session).summary(month, employees)

    async def _teachers(self, employees: list[Employee]) -> set:
        roles = {r.id: r.default_role_code for r in (await self.session.execute(select(JobRole).where(JobRole.id.in_({e.job_role_id for e in employees} or {uuid.uuid4()})))).scalars()}
        return {e.id for e in employees if roles.get(e.job_role_id) == Roles.TEACHER}

    async def _security_held(self, employee_id: uuid.UUID, exclude_run: uuid.UUID) -> Decimal:
        """Security already held: what the person brought in (opening) plus every SECURITY line of paid payrolls."""
        paid = (await self.session.execute(select(func.coalesce(func.sum(PayslipLine.amount), 0)).join(Payslip, Payslip.id == PayslipLine.payslip_id)
                                           .join(PayrollRun, PayrollRun.id == Payslip.run_id)
                                           .where(Payslip.employee_id == employee_id, PayslipLine.source == "SECURITY", PayrollRun.status == S.PAID,
                                                  PayrollRun.id != exclude_run, Payslip.held.is_(False)))).scalar_one()
        setting = await self.session.get(EmployeePayrollSetting, employee_id)
        return rules.q2(paid) + rules.q2(setting.security_opening if setting else 0)

    def _apply_attendance(self, slip: Payslip, att: dict | None, teacher: bool, casual_on_leave: int = 0) -> None:
        """Absences and late / early marks from staff attendance, unless the administration overrode them."""
        if not slip.attendance_override:
            if settings.payroll_attendance_deductions and att and att.get("recorded", 0) > 0:
                missing = att["missing_out"] if settings.payroll_missing_checkout_counts else 0
                slip.absent_days = Decimal(att["absent"])
                slip.late_marks = att["late"] + att["early"] + missing
                slip.attendance_note = (f"Present {att['present']} of {att['scheduled']} working days so far"
                                        + (f" ({att['excused']} excused)" if att["excused"] else "")
                                        + f"; late {att['late']}, left early {att['early']}" + (f", no check-out {missing}" if missing else "") + ".")
            else:
                slip.absent_days, slip.late_marks = Decimal(0), 0
                slip.attendance_note = ("No attendance recorded this month, so nothing is deducted for absence." if settings.payroll_attendance_deductions else None)
        allowed = Decimal(settings.payroll_casual_leaves_teacher if teacher else settings.payroll_casual_leaves_staff)
        # Approved casual-leave applications (app/timekeeping/leave.py) are paid days already; they use up the allowance.
        allowed = max(Decimal(0), allowed - Decimal(casual_on_leave))
        slip.casual_leaves = min(Decimal(str(slip.absent_days)), allowed)

    async def _late_absences(self, emp: Employee, run: PayrollRun, teacher: bool) -> tuple[str, Decimal, uuid.UUID] | None:
        """Absences and late marks in LAST month that came after last month's payroll was worked out, so were never deducted.
        Last month's payslip is compared with last month's full attendance now; only the extra days are taken, once, at last
        month's day rate. Nothing is carried when the administration overrode last month's attendance, and never a refund."""
        if not settings.payroll_attendance_deductions:
            return None
        prev_month = (rules.month_start(run.month) - timedelta(days=1)).replace(day=1)
        row = (await self.session.execute(select(Payslip, PayrollRun).join(PayrollRun, PayrollRun.id == Payslip.run_id)
                                          .where(Payslip.employee_id == emp.id, PayrollRun.month == prev_month, PayrollRun.status == S.PAID))).first()
        if row is None or row[0].attendance_override:
            return None
        prev = row[0]
        att = (await self._attendance(prev_month, [emp])).get(emp.id)
        if not att or not att.get("recorded"):
            return None
        allowed = Decimal(settings.payroll_casual_leaves_teacher if teacher else settings.payroll_casual_leaves_staff)
        absent_now = Decimal(att["absent"])
        charged_now = absent_now - min(absent_now, allowed)
        charged_then = max(Decimal(0), Decimal(str(prev.absent_days)) - Decimal(str(prev.casual_leaves)))
        extra_absent = max(Decimal(0), charged_now - charged_then)
        per_day = int(settings.payroll_late_marks_per_day)
        marks_now = att["late"] + att["early"] + (att["missing_out"] if settings.payroll_missing_checkout_counts else 0)
        extra_late = max(0, rules.marks_to_days(marks_now, per_day) - rules.marks_to_days(int(prev.late_marks or 0), per_day))
        days = extra_absent + extra_late
        if days <= 0:
            return None
        owed = rules.unpaid_deduction(prev.base_salary, int(prev.working_days or 26), days)
        taken = (await self.session.execute(select(func.coalesce(func.sum(PayslipLine.amount), 0)).join(Payslip, Payslip.id == PayslipLine.payslip_id)
                                            .join(PayrollRun, PayrollRun.id == Payslip.run_id)
                                            .where(PayslipLine.source == "LATE_ABSENT", PayslipLine.ref_id == prev.id,
                                                   PayrollRun.status != S.CANCELLED, PayrollRun.id != run.id))).scalar_one()
        amount = rules.q2(owed - rules.q2(taken))
        if amount <= 0:
            return None
        parts = ([f"{float(extra_absent):g} day{'s' if extra_absent != 1 else ''} absent"] if extra_absent else []) + \
                ([f"{extra_late} day{'s' if extra_late != 1 else ''} for late marks"] if extra_late else [])
        return (f"{prev_month:%B %Y}, after that payroll was paid: " + " and ".join(parts), amount, prev.id)

    async def _fill(self, slip: Payslip, emp: Employee, run: PayrollRun, base: Decimal, att: dict | None = None, teacher: bool | None = None) -> None:
        """(Re)build every line of one payslip: salary, pay items, bonuses, absences, late marks, unpaid days, adjustment, security and advances."""
        await self.session.execute(PayslipLine.__table__.delete().where(PayslipLine.payslip_id == slip.id))
        if teacher is None:
            teacher = emp.id in await self._teachers([emp])
        if att is None and not slip.attendance_override:
            att = (await self._attendance(run.month, [emp])).get(emp.id)
        from app.timekeeping.leave import approved_days_in_month

        casual_on_leave = await approved_days_in_month(self.session, emp.id, "CASUAL", run.month)
        unpaid_on_leave = await approved_days_in_month(self.session, emp.id, "UNPAID", run.month)
        self._apply_attendance(slip, att, teacher, casual_on_leave)
        month, dim = run.month, rules.days_in(run.month)
        wd = int(slip.working_days or run.working_days or settings.payroll_default_working_days or rules.days_in(run.month))
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
        from app.teaching.service import covers_payable    # classes taken for an absent teacher (Attendance -> Teachers)

        for c in await covers_payable(self.session, emp.id, rules.month_end(month), run.id):
            earn.append((LineKind.EARNING, "COVER", f"Class cover: {c.description}"[:160], rules.q2(c.amount), c.id))
        adj = rules.q2(slip.adjustment)
        if adj > 0:
            earn.append((LineKind.EARNING, "ADJUSTMENT", slip.adjustment_note or "Adjustment", adj, None))
        absent, casual = Decimal(str(slip.absent_days)), Decimal(str(slip.casual_leaves))
        if absent - casual > 0:
            ded.append((LineKind.DEDUCTION, "ABSENT", f"Absent {float(absent):g} day{'s' if absent != 1 else ''}" + (f", {float(casual):g} casual leave allowed" if casual else "")
                        + f": {float(absent - casual):g} day{'s' if absent - casual != 1 else ''} at basic / {wd}", rules.unpaid_deduction(base, wd, absent - casual), None))
        per_day = int(settings.payroll_late_marks_per_day)
        late_days = rules.marks_to_days(int(slip.late_marks or 0), per_day)
        if late_days > 0:
            ded.append((LineKind.DEDUCTION, "LATE", f"{slip.late_marks} late arrivals / early leavings (every {per_day} = 1 day): {late_days} day{'s' if late_days != 1 else ''}",
                        rules.unpaid_deduction(base, wd, late_days), None))
        carried = await self._late_absences(emp, run, teacher)
        if carried is not None:
            ded.append((LineKind.DEDUCTION, "LATE_ABSENT", carried[0], carried[1], carried[2]))
        if Decimal(str(slip.unpaid_days)) > 0:
            ded.append((LineKind.DEDUCTION, "UNPAID", f"Unpaid leave ({float(slip.unpaid_days):g} day{'s' if float(slip.unpaid_days) != 1 else ''})", rules.unpaid_deduction(base, wd, slip.unpaid_days), None))
        if unpaid_on_leave > 0:
            ded.append((LineKind.DEDUCTION, "UNPAID_LEAVE", f"Unpaid leave, approved application ({unpaid_on_leave} day{'s' if unpaid_on_leave != 1 else ''}) at basic / {wd}",
                        rules.unpaid_deduction(base, wd, Decimal(unpaid_on_leave)), None))
        if adj < 0:
            ded.append((LineKind.DEDUCTION, "ADJUSTMENT", slip.adjustment_note or "Adjustment", -adj, None))
        setting = await self.session.get(EmployeePayrollSetting, emp.id)
        pct = Decimal(str(setting.security_percent)) if setting is not None and setting.security_percent is not None else Decimal(str(settings.payroll_security_percent))
        if pct > 0:
            held = await self._security_held(emp.id, run.id)
            take = min(rules.q2(Decimal(str(base)) * pct / 100), rules.q2(base) - held)
            if take > 0:
                ded.append((LineKind.DEDUCTION, "SECURITY", f"Security deposit ({float(pct):g}% of basic): Rs. {float(held + take):,.0f} of Rs. {float(base):,.0f} held after this", take, None))
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
    async def _schedule_days(self, month: date, employees: list[Employee]) -> dict[uuid.UUID, int]:
        from app.timekeeping.service import TimekeepingService
        return await TimekeepingService(self.session).scheduled_days(month, employees)

    async def prepare(self, month: date, *, actor_id: uuid.UUID, working_days: int | None = None) -> tuple[PayrollRun, list[str]]:
        """A day's pay = basic / working days. The working days are: the number typed for this payroll (same for everyone),
        else the institute's fixed number from Settings, else (Settings = 0, the default) EACH PERSON'S OWN working days in the
        month from their timing, so someone who works 8 days a month earns basic / 8 a day. The run then stores 0."""
        month = rules.month_start(month)
        working_days = int(working_days or settings.payroll_default_working_days or 0)
        if working_days and not 1 <= working_days <= rules.days_in(month):
            raise BusinessRuleViolationError(f"Working days must be between 1 and {rules.days_in(month)}.")
        today = institute_today()
        if month > rules.month_start(today).replace(day=1) and (month.year, month.month) > ((today.year, today.month + 1) if today.month < 12 else (today.year + 1, 1)):
            raise BusinessRuleViolationError("You can prepare payroll up to next month, not further ahead.")
        if (today.year - month.year) * 12 + today.month - month.month > 24:
            raise BusinessRuleViolationError("That month is more than two years ago.")
        if (await self.session.execute(select(func.count()).select_from(PayrollRun).where(PayrollRun.month == month, PayrollRun.status != S.CANCELLED))).scalar_one():
            raise BusinessRuleViolationError("There is already a payroll run for that month. Open it, or cancel it first.")
        people, salaries = await self._eligible(month)
        run = PayrollRun(month=month, status=S.DRAFT, created_by=actor_id, working_days=working_days)
        self.session.add(run)
        await self.session.flush()
        await self.session.refresh(run)
        skipped = []
        att = await self._attendance(month, [e for e, _ in people])
        teachers = await self._teachers([e for e, _ in people])
        own = {} if working_days else await self._schedule_days(month, [e for e, _ in people])
        for emp, title in people:
            base = salaries.get(emp.id)
            if base is None or base <= 0:
                skipped.append(f"{emp.first_name} {emp.last_name} (no salary on record)")
                continue
            slip = Payslip(run_id=run.id, employee_id=emp.id, employee_name=f"{emp.first_name} {emp.last_name}", employee_code=emp.employee_code, job_title=title, department=emp.department,
                           base_salary=base, days_in_month=rules.days_in(month), days_payable=rules.payable_days(emp.hire_date, month),
                           working_days=working_days or own.get(emp.id) or rules.days_in(month), gross=0, deductions=0, net=0)
            self.session.add(slip)
            await self.session.flush()
            await self._fill(slip, emp, run, base, att.get(emp.id), emp.id in teachers)
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
        att = await self._attendance(run.month, [e for e, _ in people])
        teachers = await self._teachers([e for e, _ in people])
        # Someone added since the payroll was prepared gets their own working days too. Existing payslips keep theirs
        # (set when prepared, or changed by hand with Adjust), so a recalculation never undoes a person's decision.
        own = {} if run.working_days else await self._schedule_days(run.month, [e for e, _ in people if e.id not in have])
        for emp, title in people:
            base = salaries.get(emp.id)
            if base is None or base <= 0:
                continue
            slip = have.get(emp.id)
            if slip is None:
                slip = Payslip(run_id=run.id, employee_id=emp.id, employee_name=f"{emp.first_name} {emp.last_name}", employee_code=emp.employee_code, job_title=title, department=emp.department,
                               base_salary=base, days_in_month=rules.days_in(run.month), days_payable=rules.payable_days(emp.hire_date, run.month),
                               working_days=run.working_days or own.get(emp.id) or rules.days_in(run.month),
                               gross=0, deductions=0, net=0)
                self.session.add(slip)
                await self.session.flush()
            await self._fill(slip, emp, run, base, att.get(emp.id), emp.id in teachers)
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
        if changes.get("working_days") is not None:
            wd = int(changes["working_days"])
            if not 1 <= wd <= slip.days_in_month:
                raise BusinessRuleViolationError(f"Working days must be between 1 and {slip.days_in_month}.")
            slip.working_days = wd
        if changes.get("reset_attendance"):
            slip.attendance_override = False
        else:
            if changes.get("absent_days") is not None:
                a = Decimal(str(changes["absent_days"]))
                if a < 0 or a > slip.days_in_month or (a * 2) % 1 != 0:
                    raise BusinessRuleViolationError(f"Absent days must be between 0 and {slip.days_in_month}, in whole or half days.")
                slip.absent_days, slip.attendance_override = a, True
            if changes.get("late_marks") is not None:
                m = int(changes["late_marks"])
                if not 0 <= m <= 62:
                    raise BusinessRuleViolationError("Late / early marks must be between 0 and 62.")
                slip.late_marks, slip.attendance_override = m, True
            if slip.attendance_override:
                slip.attendance_note = "Set by the administration."
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

    async def _run_locked(self, run_id: uuid.UUID) -> PayrollRun:
        run = (await self.session.execute(select(PayrollRun).where(PayrollRun.id == run_id).with_for_update()
                                          .execution_options(populate_existing=True))).scalar_one_or_none()
        if run is None:
            raise ResourceNotFoundError("Payroll run not found.")
        return run

    async def submit(self, run_id: uuid.UUID, *, actor_id: uuid.UUID) -> PayrollRun:
        """The accounts office has checked the draft and sends it to the administration. It is locked until approved or sent back."""
        run = await self._run_locked(run_id)
        if run.status != S.DRAFT:
            raise BusinessRuleViolationError("Only a draft can be sent for approval.")
        slips = [s for s in await self._payslips(run.id) if not s.held]
        if not slips:
            raise BusinessRuleViolationError("Every payslip is held back, so there is nothing to send for approval.")
        negative = [s.employee_name for s in slips if Decimal(str(s.net)) < 0]
        if negative:
            raise BusinessRuleViolationError("These people would be paid less than nothing: " + ", ".join(negative[:5]) + ". Adjust their deductions or hold their payslip.")
        await self._totals(run)
        run.status, run.submitted_by, run.submitted_at = S.SUBMITTED, actor_id, datetime.now(timezone.utc)
        await self.audit.log(actor_user_id=actor_id, action="payroll.submitted", entity_type="payroll_run", entity_id=run.id, metadata={"net": str(run.total_net)})
        for admin in await self._approver_ids():
            if admin != actor_id:
                await create_notification(self.session, user_id=admin, category=NotificationCategory.PAYROLL, title=f"Payroll to approve: {run.month:%B %Y}",
                                          message=f"{run.employees_count} employees, net Rs. {float(run.total_net):,.0f}.", link="/dashboard/admin/payroll")
        return run

    async def send_back(self, run_id: uuid.UUID, *, admin_id: uuid.UUID, reason: str) -> PayrollRun:
        """The administration returns a submitted run to the accounts office for corrections."""
        run = await self._run_locked(run_id)
        if run.status != S.SUBMITTED:
            raise BusinessRuleViolationError("Only a run waiting for approval can be sent back.")
        if len((reason or "").strip()) < 3:
            raise BusinessRuleViolationError("Say what needs correcting.")
        run.status, run.returned_reason = S.DRAFT, reason.strip()
        await self.audit.log(actor_user_id=admin_id, action="payroll.sent_back", entity_type="payroll_run", entity_id=run.id, metadata={"reason": reason.strip()})
        if run.submitted_by and run.submitted_by != admin_id:
            await create_notification(self.session, user_id=run.submitted_by, category=NotificationCategory.PAYROLL, title=f"Payroll sent back: {run.month:%B %Y}",
                                      message=reason.strip(), link="/dashboard/accounts/payroll")
        return run

    async def approve(self, run_id: uuid.UUID, *, admin_id: uuid.UUID) -> PayrollRun:
        run = await self._run_locked(run_id)
        if run.status != S.SUBMITTED:
            raise BusinessRuleViolationError("The accounts office must send this payroll for approval first." if run.status == S.DRAFT else "Only a payroll waiting for approval can be approved.")
        slips = [s for s in await self._payslips(run.id) if not s.held]
        if not slips:
            raise BusinessRuleViolationError("Every payslip is held back, so there is nothing to approve.")
        negative = [s.employee_name for s in slips if Decimal(str(s.net)) < 0]
        if negative:
            raise BusinessRuleViolationError("These people would be paid less than nothing: " + ", ".join(negative[:5]) + ". Adjust their deductions or hold their payslip.")
        await self._totals(run)
        run.status, run.approved_by, run.approved_at = S.APPROVED, admin_id, datetime.now(timezone.utc)
        await self.audit.log(actor_user_id=admin_id, action="payroll.approved", entity_type="payroll_run", entity_id=run.id, metadata={"net": str(run.total_net)})
        if run.submitted_by and run.submitted_by != admin_id:
            await create_notification(self.session, user_id=run.submitted_by, category=NotificationCategory.PAYROLL, title=f"Payroll approved: {run.month:%B %Y}",
                                      message="It can be paid now.", link="/dashboard/accounts/payroll")
        return run

    async def cancel(self, run_id: uuid.UUID, *, actor_id: uuid.UUID, reason: str) -> PayrollRun:
        run = await self._run_locked(run_id)
        if run.status not in (S.DRAFT, S.SUBMITTED, S.APPROVED):
            raise BusinessRuleViolationError("Only a payroll run that has not been paid can be cancelled." if run.status == S.PAID else "This run is already cancelled.")
        if len((reason or "").strip()) < 3:
            raise BusinessRuleViolationError("Say why you are cancelling it.")
        run.status, run.cancelled_by, run.cancelled_at, run.cancel_reason = S.CANCELLED, actor_id, datetime.now(timezone.utc), reason.strip()
        await self.audit.log(actor_user_id=actor_id, action="payroll.cancelled", entity_type="payroll_run", entity_id=run.id, metadata={"reason": reason.strip()})
        return run

    async def pay(self, run_id: uuid.UUID, *, actor_id: uuid.UUID, method: PaymentMethod, reference: str | None, paid_date: date) -> PayrollRun:
        run = await self._run_locked(run_id)
        if run.status != S.APPROVED:
            raise BusinessRuleViolationError("Only an approved payroll run can be paid.")
        if method in NEEDS_REFERENCE and not (reference or "").strip():
            raise BusinessRuleViolationError("Enter the cheque number or the bank transaction reference.")
        if paid_date > institute_today():
            raise BusinessRuleViolationError("The payment date cannot be in the future.")
        from app.accounts.period import ensure_open

        await ensure_open(self.session, paid_date, "This salary payment")
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
        run = await self._run_locked(run_id)
        if run.status != S.PAID:
            raise BusinessRuleViolationError("Only a paid payroll run can have its payment reversed.")
        e = await self.session.get(Expense, run.expense_id) if run.expense_id else None
        from app.accounts.period import ensure_open

        # Voiding the salary expense changes the month it was booked in, so that month must still be open.
        await ensure_open(self.session, e.expense_date if e is not None else run.paid_date, "This salary payment")
        for s in [s for s in await self._payslips(run.id) if not s.held]:
            for ln in (await self.session.execute(select(PayslipLine).where(PayslipLine.payslip_id == s.id, PayslipLine.source == "ADVANCE"))).scalars():
                adv = await self.session.get(SalaryAdvance, ln.ref_id)
                if adv is not None:
                    adv.balance = min(Decimal(str(adv.amount)), Decimal(str(adv.balance)) + Decimal(str(ln.amount)))
                    adv.status = AdvanceStatus.ACTIVE
        if e is not None:
            e.status, e.voided_at, e.void_reason = ExpenseStatus.VOIDED, datetime.now(timezone.utc), f"Payroll payment reversed: {reason.strip()}"
        run.status, run.paid_by, run.paid_at, run.paid_date, run.payment_method, run.payment_reference, run.expense_id = S.APPROVED, None, None, None, None, None, None
        await self.audit.log(actor_user_id=admin_id, action="payroll.payment_reversed", entity_type="payroll_run", entity_id=run.id, metadata={"reason": reason.strip()})
        return run

    # ------------------------------------------------------------------ reading
    @staticmethod
    def _slip_row(s: Payslip) -> dict:
        per_day = int(settings.payroll_late_marks_per_day)
        return {"id": s.id, "employee_id": s.employee_id, "employee_name": s.employee_name, "employee_code": s.employee_code, "job_title": s.job_title, "base_salary": _f(s.base_salary), "days_payable": s.days_payable,
                "working_days": s.working_days, "absent_days": _f(s.absent_days), "casual_leaves": _f(s.casual_leaves), "late_marks": s.late_marks,
                "late_days": rules.marks_to_days(int(s.late_marks or 0), per_day), "late_marks_per_day": per_day, "attendance_override": s.attendance_override,
                "attendance_note": s.attendance_note, "day_rate": _f(Decimal(str(s.base_salary)) / (s.working_days or 26)),
                "days_in_month": s.days_in_month, "unpaid_days": _f(s.unpaid_days), "adjustment": _f(s.adjustment), "adjustment_note": s.adjustment_note, "held": s.held, "held_reason": s.held_reason, "released_on": s.released_on,
                "gross": _f(s.gross), "deductions": _f(s.deductions), "net": _f(s.net)}

    async def _run_row(self, r: PayrollRun) -> dict:
        names = await self.accounts._names({r.created_by, r.submitted_by, r.approved_by, r.paid_by})
        exp = await self.session.get(Expense, r.expense_id) if r.expense_id else None
        return {"id": r.id, "reference": run_ref(r), "month": r.month, "month_label": r.month.strftime("%B %Y"), "status": r.status, "working_days": r.working_days, "employees_count": r.employees_count, "total_gross": _f(r.total_gross),
                "total_deductions": _f(r.total_deductions), "total_net": _f(r.total_net), "created_by_name": names.get(r.created_by), "approved_by_name": names.get(r.approved_by), "approved_at": r.approved_at,
                "paid_by_name": names.get(r.paid_by), "paid_at": r.paid_at, "paid_date": r.paid_date, "payment_method": r.payment_method, "payment_reference": r.payment_reference,
                "expense_reference": expense_ref(exp) if exp else None, "cancel_reason": r.cancel_reason, "created_at": r.created_at,
                "submitted_by_name": names.get(r.submitted_by), "submitted_at": r.submitted_at, "returned_reason": r.returned_reason}

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
    # ------------------------------------------------------------------ security deposits
    async def my_security(self, user_id: uuid.UUID) -> dict | None:
        """One person's own security deposit: what is held now, the target (one basic salary), each month's cut, and any pay-back."""
        emp = (await self.session.execute(select(Employee).where(Employee.user_id == user_id))).scalars().first()
        if emp is None:
            return None
        st = await self.session.get(EmployeePayrollSetting, emp.id)
        pct = Decimal(str(st.security_percent)) if st is not None and st.security_percent is not None else Decimal(str(settings.payroll_security_percent))
        base = (await self._salaries([emp.id], institute_today())).get(emp.id) or Decimal(0)
        cuts = (await self.session.execute(select(PayrollRun.month, PayslipLine.amount).join(Payslip, Payslip.id == PayslipLine.payslip_id)
                                           .join(PayrollRun, PayrollRun.id == Payslip.run_id)
                                           .where(Payslip.employee_id == emp.id, PayslipLine.source == "SECURITY", PayrollRun.status == S.PAID, Payslip.held.is_(False))
                                           .order_by(PayrollRun.month.desc()))).all()
        opening = rules.q2(st.security_opening if st else 0)
        released = rules.q2(st.security_released if st else 0)
        collected = rules.q2(sum((Decimal(str(a)) for _, a in cuts), Decimal(0))) + opening
        held = max(Decimal(0), collected - released)
        return {"percent": _f(pct), "own_rate": st is not None and st.security_percent is not None, "held": _f(held), "target": _f(base),
                "remaining": _f(max(Decimal(0), rules.q2(base) - collected)) if not released else 0.0, "opening": _f(opening),
                "released": _f(released), "released_on": st.security_released_on if st else None,
                "months": [{"month": m, "month_label": m.strftime("%B %Y"), "amount": _f(a)} for m, a in cuts]}

    async def security_report(self) -> dict:
        people = (await self.session.execute(select(Employee, JobRole.title).join(JobRole, JobRole.id == Employee.job_role_id).where(Employee.status.in_(ACTIVE))
                                             .order_by(Employee.first_name, Employee.last_name))).all()
        salaries = await self._salaries([e.id for e, _ in people], institute_today())
        settings_rows = {s.employee_id: s for s in (await self.session.execute(select(EmployeePayrollSetting))).scalars()}
        held_rows = dict((await self.session.execute(select(Payslip.employee_id, func.sum(PayslipLine.amount)).join(PayslipLine, PayslipLine.payslip_id == Payslip.id)
                                                     .join(PayrollRun, PayrollRun.id == Payslip.run_id)
                                                     .where(PayslipLine.source == "SECURITY", PayrollRun.status == S.PAID, Payslip.held.is_(False))
                                                     .group_by(Payslip.employee_id))).all())
        rows = []
        for e, title in people:
            st = settings_rows.get(e.id)
            base = salaries.get(e.id) or Decimal(0)
            held = max(Decimal(0), rules.q2(held_rows.get(e.id) or 0) + rules.q2(st.security_opening if st else 0) - rules.q2(st.security_released if st else 0))
            pct = st.security_percent if st is not None and st.security_percent is not None else None
            rows.append({"employee_id": e.id, "name": f"{e.first_name} {e.last_name}", "employee_code": e.employee_code, "job_title": title,
                         "base_salary": _f(base), "held": _f(held), "target": _f(base), "remaining": _f(max(Decimal(0), rules.q2(base) - held)),
                         "own_percent": _f(pct) if pct is not None else None, "opening": _f(st.security_opening) if st else 0.0, "note": st.note if st else None})
        return {"institute_percent": float(settings.payroll_security_percent), "total_held": round(sum(r["held"] for r in rows), 2), "people": rows}

    async def release_payslip(self, run_id: uuid.UUID, payslip_id: uuid.UUID, *, method: PaymentMethod, reference: str | None, paid_date: date, actor_id: uuid.UUID) -> dict:
        """A payslip that was held back. While the month is still being approved, releasing it simply puts it back in the month's payment.
        Once the month has been paid, it is paid on its own now: one expense in the books, and the slip remembers the day."""
        from app.accounts.period import ensure_open

        run = await self._run(run_id)
        slip = next((s for s in await self._payslips(run_id) if s.id == payslip_id), None)
        if slip is None:
            raise ResourceNotFoundError("Payslip not found.")
        if not slip.held:
            raise BusinessRuleViolationError("This payslip is not held back.")
        if run.status in (S.DRAFT, S.SUBMITTED, S.CANCELLED):
            raise BusinessRuleViolationError("Release it only once the payroll is approved; until then, untick 'hold back' on the payslip (in a draft).")
        if run.status == S.APPROVED:
            slip.held, slip.held_reason = False, None
            await self.session.flush()
            return {"released": True, "paid_separately": False}
        if paid_date > institute_today():
            raise BusinessRuleViolationError("The payment date cannot be in the future.")
        if method in NEEDS_REFERENCE and not (reference or "").strip():
            raise BusinessRuleViolationError("Enter the cheque number or the bank transaction reference.")
        await ensure_open(self.session, paid_date, "This payment")
        net = rules.q2(slip.net)
        if net <= 0:
            raise BusinessRuleViolationError("There is nothing to pay on this payslip.")
        category = await self._salary_category()
        expense = Expense(category_id=category.id, title=f"Salary (held, paid now): {slip.employee_name}", description=f"Payslip of {run_ref(run)} that had been held back.",
                          amount=net, expense_date=paid_date, payment_method=method, reference_no=(reference or "").strip() or None, status=ExpenseStatus.APPROVED,
                          source=ExpenseSource.PAYROLL, recorded_by=actor_id, decided_by=actor_id, decided_at=datetime.now(timezone.utc))
        self.session.add(expense)
        slip.held, slip.held_reason, slip.released_on = False, None, paid_date
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="payroll.held_payslip_paid", entity_type="payslip", entity_id=slip.id, metadata={"amount": str(net), "expense": str(expense.id)})
        return {"released": True, "paid_separately": True, "expense_reference": expense_ref(expense)}

    async def release_security(self, employee_id: uuid.UUID, *, method: PaymentMethod, reference: str | None, paid_date: date, actor_id: uuid.UUID) -> dict:
        """Pay a person's security deposit back (normally when they leave). It is one expense in the books, and can be done only once:
        what has been paid back is no longer 'held'."""
        from app.accounts.period import ensure_open

        e = await self._employee(employee_id)
        self._not_own_pay(e, actor_id, "pay back a security deposit")
        if paid_date > institute_today():
            raise BusinessRuleViolationError("The date cannot be in the future.")
        if method in NEEDS_REFERENCE and not (reference or "").strip():
            raise BusinessRuleViolationError("Enter the cheque number or the bank transaction reference.")
        await ensure_open(self.session, paid_date, "This payment")
        st = await self.session.get(EmployeePayrollSetting, e.id)
        deducted = (await self.session.execute(
            select(func.coalesce(func.sum(PayslipLine.amount), 0)).join(Payslip, Payslip.id == PayslipLine.payslip_id).join(PayrollRun, PayrollRun.id == Payslip.run_id)
            .where(Payslip.employee_id == e.id, PayslipLine.source == "SECURITY", PayrollRun.status == S.PAID, Payslip.held.is_(False)))).scalar_one()
        held = rules.q2(deducted) + rules.q2(st.security_opening if st else 0) - rules.q2(st.security_released if st else 0)
        if held <= 0:
            raise BusinessRuleViolationError("Nothing is held for this person.")
        category = await self._salary_category()
        expense = Expense(category_id=category.id, title=f"Security deposit paid back: {e.first_name} {e.last_name}", description="Security deposit held from salary, returned to the employee.",
                          amount=held, expense_date=paid_date, payment_method=method, reference_no=(reference or "").strip() or None, status=ExpenseStatus.APPROVED,
                          source=ExpenseSource.PAYROLL, recorded_by=actor_id, decided_by=actor_id, decided_at=datetime.now(timezone.utc))
        self.session.add(expense)
        if st is None:
            st = EmployeePayrollSetting(employee_id=e.id, security_opening=0, security_released=0)
            self.session.add(st)
        st.security_released = rules.q2(st.security_released or 0) + held
        st.security_released_on = paid_date
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="payroll.security_released", entity_type="employee", entity_id=e.id, metadata={"amount": str(held), "expense": str(expense.id)})
        return {"employee_id": e.id, "paid_back": _f(held), "expense_reference": expense_ref(expense)}

    async def set_pay_setting(self, employee_id: uuid.UUID, data: dict, *, actor_id: uuid.UUID) -> dict:
        e = await self._employee(employee_id)
        st = await self.session.get(EmployeePayrollSetting, e.id)
        before = {"security_percent": str(st.security_percent), "security_opening": str(st.security_opening)} if st else None
        if st is None:
            st = EmployeePayrollSetting(employee_id=e.id, security_opening=0)
            self.session.add(st)
        st.security_percent = data.get("security_percent")
        st.security_opening = rules.q2(data.get("security_opening") or 0)
        st.note = (data.get("note") or "").strip() or None
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="payroll.employee_setting", entity_type="employee", entity_id=e.id, before=before,
                             after={"security_percent": str(st.security_percent), "security_opening": str(st.security_opening), "note": st.note})
        return next(r for r in (await self.security_report())["people"] if r["employee_id"] == e.id)

    # ------------------------------------------------------------------ what the payroll owes onward
    async def withholdings(self, year: int) -> dict:
        """Money taken off salaries in a year's PAID payrolls, by head and by month: what the institute holds or must pass
        on (security deposits, deductions such as tax or provident fund, advance recoveries, unpaid days)."""
        def head_of(source: str, label: str) -> str:
            if source == "SECURITY":
                return "Security deposit"
            if source == "ADVANCE":
                return "Advance recovery"
            if source == "UNPAID":
                return "Unpaid days"
            if source == "LATE_ABSENT":
                return "Absences carried from the month before"
            return label.split(":")[0].strip()[:60] or "Deduction"

        rows = (await self.session.execute(
            select(PayrollRun.month, PayslipLine.source, PayslipLine.label, func.sum(PayslipLine.amount))
            .join(Payslip, Payslip.id == PayslipLine.payslip_id).join(PayrollRun, PayrollRun.id == Payslip.run_id)
            .where(PayrollRun.status == S.PAID, Payslip.held.is_(False), PayslipLine.kind == LineKind.DEDUCTION,
                   PayrollRun.month >= date(year, 1, 1), PayrollRun.month <= date(year, 12, 31))
            .group_by(PayrollRun.month, PayslipLine.source, PayslipLine.label))).all()
        heads: dict[str, float] = {}
        months: dict[str, dict[str, float]] = {}
        for month, source, label, amount in rows:
            name = head_of(source, label or "")
            amt = _f(amount)
            heads[name] = round(heads.get(name, 0.0) + amt, 2)
            m = months.setdefault(f"{month.year:04d}-{month.month:02d}", {})
            m[name] = round(m.get(name, 0.0) + amt, 2)
        security_now = (await self.security_report())["total_held"]
        return {"year": year, "heads": [{"name": k, "total": v} for k, v in sorted(heads.items())],
                "months": [{"month": k, "by_head": v, "total": round(sum(v.values()), 2)} for k, v in sorted(months.items())],
                "total": round(sum(heads.values()), 2), "security_held_now": security_now}
