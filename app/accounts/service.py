from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import (
    AccountCategory,
    EntryKind,
    Expense,
    ExpenseAttachment,
    ExpenseSource,
    ExpenseStatus,
    IncomeEntry,
    PaymentMethod,
    Vendor,
)
from app.audit.service import AuditService
from app.auth.models import Role, User, UserRole
from app.auth.permissions import Roles
from app.core.clock import institute_today
from app.accounts.period import ensure_open
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, ResourceNotFoundError
from app.core.storage import FileTooLargeError, UnsupportedFileTypeError, attachment_is_inline, delete_file, save_attachment, validate_attachment
from app.employees.models import Employee
from app.fees.models import FeeLedgerEntry, LedgerEntryType
from app.fines.models import Fine, FineStatus
from app.notifications.models import NotificationCategory
from app.notifications.service import create_notification

MAX_FILES = 5
MAX_AMOUNT = Decimal("100000000")
BACKDATE_DAYS = 366
NEEDS_REFERENCE = (PaymentMethod.CHEQUE, PaymentMethod.BANK_TRANSFER)


# The standard categories. The migration seeds them on a real install; this also starts any database that has none.
STANDARD_EXPENSE_CATEGORIES = [("Salaries and wages", True), ("Utilities (electricity, gas, water)", False), ("Rent", False), ("Repairs and maintenance", False), ("Stationery and supplies", False),
                               ("Equipment and furniture", False), ("Books and library", False), ("IT and internet", False), ("Transport and fuel", False), ("Events and activities", False),
                               ("Marketing and advertising", False), ("Taxes and government fees", False), ("Purchases", True), ("Miscellaneous", False)]
STANDARD_INCOME_CATEGORIES = [("Donations and grants", False), ("Canteen and cafeteria", False), ("Rent received", False), ("Hostel", False), ("Exam and other fees", False), ("Other income", False)]


def expense_ref(e: Expense) -> str:
    return f"EXP-{e.created_at.year}-{e.serial_no:05d}"


def income_ref(i: IncomeEntry) -> str:
    return f"INC-{i.created_at.year}-{i.serial_no:05d}"


def _f(v) -> float:
    return round(float(v or 0), 2)


class AccountsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ settings and people
    @staticmethod
    def config() -> dict:
        return {"expense_approval_limit": float(settings.expense_approval_limit), "financial_year_start_month": settings.financial_year_start_month, "today": institute_today()}

    async def _names(self, user_ids: set) -> dict[uuid.UUID, str]:
        user_ids = {u for u in user_ids if u}
        out: dict = {}
        if not user_ids:
            return out
        for em in (await self.session.execute(select(Employee).where(Employee.user_id.in_(user_ids)))).scalars():
            out[em.user_id] = f"{em.first_name} {em.last_name}"
        for u in (await self.session.execute(select(User).where(User.id.in_(user_ids - set(out))))).scalars():
            out[u.id] = "Administrator" if u.username.lower().startswith("admin") else u.username
        return out

    async def _admin_ids(self) -> list[uuid.UUID]:
        return list(set((await self.session.execute(
            select(User.id).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id).where(Role.code == Roles.ADMIN, User.is_active.is_(True))
        )).scalars()))

    # ------------------------------------------------------------------ categories
    async def categories(self, kind: EntryKind | None, active_only: bool) -> list[AccountCategory]:
        if not (await self.session.execute(select(func.count()).select_from(AccountCategory))).scalar_one():
            for k, rows in ((EntryKind.EXPENSE, STANDARD_EXPENSE_CATEGORIES), (EntryKind.INCOME, STANDARD_INCOME_CATEGORIES)):
                for name, system in rows:
                    self.session.add(AccountCategory(kind=k, name=name, is_system=system, is_active=True))
            await self.session.flush()
        stmt = select(AccountCategory).order_by(AccountCategory.kind, AccountCategory.is_system.desc(), AccountCategory.name)
        if kind:
            stmt = stmt.where(AccountCategory.kind == kind)
        if active_only:
            stmt = stmt.where(AccountCategory.is_active.is_(True))
        return list((await self.session.execute(stmt)).scalars())

    async def create_category(self, *, kind: EntryKind, name: str, actor_id: uuid.UUID) -> AccountCategory:
        name = name.strip()
        if (await self.session.execute(select(func.count()).select_from(AccountCategory).where(AccountCategory.kind == kind, func.lower(AccountCategory.name) == name.lower()))).scalar_one():
            raise BusinessRuleViolationError("There is already a category with that name.")
        c = AccountCategory(kind=kind, name=name, is_system=False, is_active=True)
        self.session.add(c)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="accounts.category_created", entity_type="account_category", entity_id=c.id)
        return c

    async def update_category(self, category_id: uuid.UUID, *, name: str | None, is_active: bool | None, actor_id: uuid.UUID) -> AccountCategory:
        c = await self.session.get(AccountCategory, category_id)
        if c is None:
            raise ResourceNotFoundError("Category not found.")
        if c.is_system and ((name and name.strip() != c.name) or is_active is False):
            raise BusinessRuleViolationError("This category is used by the system itself, so it cannot be renamed or switched off.")
        if name and name.strip() != c.name:
            if (await self.session.execute(select(func.count()).select_from(AccountCategory).where(AccountCategory.kind == c.kind, func.lower(AccountCategory.name) == name.strip().lower(), AccountCategory.id != c.id))).scalar_one():
                raise BusinessRuleViolationError("There is already a category with that name.")
            c.name = name.strip()
        if is_active is not None:
            c.is_active = is_active
        await self.audit.log(actor_user_id=actor_id, action="accounts.category_updated", entity_type="account_category", entity_id=c.id)
        return c

    async def _category(self, category_id: uuid.UUID, kind: EntryKind) -> AccountCategory:
        c = await self.session.get(AccountCategory, category_id)
        if c is None or c.kind != kind or not c.is_active:
            raise BusinessRuleViolationError(f"Choose an active {'expense' if kind == EntryKind.EXPENSE else 'income'} category.")
        return c

    # ------------------------------------------------------------------ vendors
    async def vendors(self, q: str | None, active_only: bool) -> list[dict]:
        stmt = select(Vendor).order_by(Vendor.name)
        if active_only:
            stmt = stmt.where(Vendor.is_active.is_(True))
        if q and q.strip():
            stmt = stmt.where(Vendor.name.ilike(f"%{q.strip()}%"))
        vendors = list((await self.session.execute(stmt.limit(500))).scalars())
        spent = {vid: _f(t) for vid, t in (await self.session.execute(
            select(Expense.vendor_id, func.sum(Expense.amount)).where(Expense.vendor_id.is_not(None), Expense.status == ExpenseStatus.APPROVED).group_by(Expense.vendor_id))).all()}
        return [{"id": v.id, "name": v.name, "contact_person": v.contact_person, "phone": v.phone, "email": v.email, "address": v.address, "tax_number": v.tax_number,
                 "is_active": v.is_active, "total_spent": spent.get(v.id, 0.0)} for v in vendors]

    async def create_vendor(self, data: dict, *, actor_id: uuid.UUID) -> Vendor:
        data["name"] = data["name"].strip()
        if (await self.session.execute(select(func.count()).select_from(Vendor).where(func.lower(Vendor.name) == data["name"].lower()))).scalar_one():
            raise BusinessRuleViolationError("There is already a vendor with that name.")
        v = Vendor(**data)
        self.session.add(v)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="accounts.vendor_created", entity_type="vendor", entity_id=v.id)
        return v

    async def update_vendor(self, vendor_id: uuid.UUID, changes: dict, *, actor_id: uuid.UUID) -> Vendor:
        v = await self.session.get(Vendor, vendor_id)
        if v is None:
            raise ResourceNotFoundError("Vendor not found.")
        for k, val in changes.items():
            setattr(v, k, val.strip() if isinstance(val, str) else val)
        await self.audit.log(actor_user_id=actor_id, action="accounts.vendor_updated", entity_type="vendor", entity_id=v.id)
        return v

    # ------------------------------------------------------------------ expenses
    @staticmethod
    def _check_money(amount: Decimal, when: date, what: str) -> None:
        if amount <= 0 or amount > MAX_AMOUNT:
            raise BusinessRuleViolationError("The amount must be more than 0 and at most Rs. 100,000,000.")
        today = institute_today()
        if when > today:
            raise BusinessRuleViolationError(f"The {what} date cannot be in the future.")
        if when < today - timedelta(days=BACKDATE_DAYS):
            raise BusinessRuleViolationError(f"The {what} date is more than a year ago. Check it.")

    async def create_expense(self, *, user_id: uuid.UUID, is_approver: bool, data: dict, files: list[tuple[str, bytes, str | None]]) -> Expense:
        amount = Decimal(str(data["amount"])).quantize(Decimal("0.01"))
        self._check_money(amount, data["expense_date"], "expense")
        if not data["title"].strip():
            raise BusinessRuleViolationError("Give the expense a title.")
        category = await self._category(data["category_id"], EntryKind.EXPENSE)
        if category.is_system:
            raise BusinessRuleViolationError(f"\u201c{category.name}\u201d is filled in automatically by the system. Choose another category.")
        if data.get("vendor_id"):
            vendor = await self.session.get(Vendor, data["vendor_id"])
            if vendor is None or not vendor.is_active:
                raise BusinessRuleViolationError("Choose an active vendor, or leave it empty.")
        if data["payment_method"] in NEEDS_REFERENCE and not (data.get("reference_no") or "").strip():
            raise BusinessRuleViolationError("Enter the cheque number or the bank transaction reference.")
        if len(files) > MAX_FILES:
            raise BusinessRuleViolationError(f"You can attach at most {MAX_FILES} files.")
        for name, content, ctype in files:                                    # every file is checked before anything is saved
            try:
                validate_attachment(content=content, original_filename=name, declared_content_type=ctype)
            except (UnsupportedFileTypeError, FileTooLargeError) as exc:
                raise BusinessRuleViolationError(f"\u201c{name}\u201d: {exc}") from exc
        await ensure_open(self.session, data["expense_date"], "This expense")
        over_limit = amount > Decimal(str(settings.expense_approval_limit))
        # Maker-checker: above the limit, even an administrator's own expense waits for ANOTHER
        # administrator (if there is one), so nobody signs off their own large spending.
        other_admins = [a for a in await self._admin_ids() if a != user_id] if over_limit and is_approver else []
        needs_approval = over_limit and (not is_approver or bool(other_admins))
        e = Expense(category_id=category.id, vendor_id=data.get("vendor_id"), title=data["title"].strip(), description=(data.get("description") or "").strip() or None, amount=amount,
                    expense_date=data["expense_date"], payment_method=data["payment_method"], reference_no=(data.get("reference_no") or "").strip() or None,
                    status=ExpenseStatus.PENDING_APPROVAL if needs_approval else ExpenseStatus.APPROVED, source=ExpenseSource.MANUAL, recorded_by=user_id)
        if not needs_approval and is_approver:
            e.decided_by, e.decided_at = user_id, datetime.now(timezone.utc)
        self.session.add(e)
        await self.session.flush()
        await self.session.refresh(e)
        saved: list[str] = []
        try:
            for name, content, ctype in files:
                key, display, content_type = save_attachment(entity_id=e.id, content=content, original_filename=name, declared_content_type=ctype, folder="expenses")
                saved.append(key)
                self.session.add(ExpenseAttachment(expense_id=e.id, original_name=display, storage_key=key, content_type=content_type, size_bytes=len(content)))
        except Exception:
            for k in saved:
                delete_file(k)
            raise
        await self.audit.log(actor_user_id=user_id, action="accounts.expense_recorded", entity_type="expense", entity_id=e.id, metadata={"amount": str(amount), "pending": needs_approval})
        if needs_approval:
            who = (await self._names({user_id})).get(user_id, "Someone")
            for admin in await self._admin_ids():
                await create_notification(self.session, user_id=admin, category=NotificationCategory.FINANCE, title=f"Expense waiting for approval: Rs. {amount:,.0f}",
                                          message=f"{who}: {e.title}. It is above the approval limit of Rs. {float(settings.expense_approval_limit):,.0f}.", link="/dashboard/admin/finance?open=expenses")
        return e

    async def _expense(self, expense_id: uuid.UUID) -> Expense:
        e = await self.session.get(Expense, expense_id)
        if e is None:
            raise ResourceNotFoundError("Expense not found.")
        return e

    async def decide_expense(self, expense_id: uuid.UUID, *, admin_id: uuid.UUID, approve: bool, note: str | None) -> Expense:
        e = await self._expense(expense_id)
        from app.core.controls import refuse_self

        refuse_self(admin_id, e.recorded_by, "You recorded this expense, so another administrator must approve or reject it.")
        if e.status != ExpenseStatus.PENDING_APPROVAL:
            raise BusinessRuleViolationError("This expense is not waiting for approval.")
        if approve:
            await ensure_open(self.session, e.expense_date, "This expense")
        if not approve and len((note or "").strip()) < 3:
            raise BusinessRuleViolationError("Say why you are rejecting it.")
        e.status = ExpenseStatus.APPROVED if approve else ExpenseStatus.REJECTED
        e.decided_by, e.decided_at, e.decision_note = admin_id, datetime.now(timezone.utc), (note or "").strip() or None
        await self.audit.log(actor_user_id=admin_id, action="accounts.expense_approved" if approve else "accounts.expense_rejected", entity_type="expense", entity_id=e.id)
        if e.recorded_by and e.recorded_by != admin_id:
            await create_notification(self.session, user_id=e.recorded_by, category=NotificationCategory.FINANCE, title=f"Expense {'approved' if approve else 'rejected'}: {expense_ref(e)}",
                                      message=f"{e.title} (Rs. {float(e.amount):,.0f})" + (f" \u2014 {e.decision_note}" if e.decision_note else ""), link="/dashboard/accounts/finance?open=expenses")
        return e

    async def void_expense(self, expense_id: uuid.UUID, *, admin_id: uuid.UUID, reason: str) -> Expense:
        e = await self._expense(expense_id)
        if e.status != ExpenseStatus.APPROVED:
            raise BusinessRuleViolationError("Only an approved expense can be voided.")
        if e.source != ExpenseSource.MANUAL:
            raise BusinessRuleViolationError("This expense was created by a purchase or a salary run. Reverse it there.")
        await ensure_open(self.session, e.expense_date, "This expense")
        e.status, e.voided_at, e.void_reason = ExpenseStatus.VOIDED, datetime.now(timezone.utc), reason.strip()
        await self.audit.log(actor_user_id=admin_id, action="accounts.expense_voided", entity_type="expense", entity_id=e.id, metadata={"reason": reason.strip()})
        return e

    async def _expense_out(self, rows: list[Expense]) -> list[dict]:
        if not rows:
            return []
        cats = {c.id: c.name for c in (await self.session.execute(select(AccountCategory))).scalars()}
        vendors = {v.id: v.name for v in (await self.session.execute(select(Vendor).where(Vendor.id.in_([r.vendor_id for r in rows if r.vendor_id])))).scalars()} if any(r.vendor_id for r in rows) else {}
        names = await self._names({r.recorded_by for r in rows} | {r.decided_by for r in rows})
        atts: dict = {}
        for a in (await self.session.execute(select(ExpenseAttachment).where(ExpenseAttachment.expense_id.in_([r.id for r in rows])).order_by(ExpenseAttachment.created_at))).scalars():
            atts.setdefault(a.expense_id, []).append({"id": a.id, "original_name": a.original_name, "content_type": a.content_type, "size_bytes": a.size_bytes,
                                                      "is_image": a.content_type.startswith("image/"), "inline": attachment_is_inline(a.content_type)})
        return [{"id": e.id, "reference": expense_ref(e), "category_id": e.category_id, "category_name": cats.get(e.category_id, "?"), "vendor_id": e.vendor_id, "vendor_name": vendors.get(e.vendor_id),
                 "title": e.title, "description": e.description, "amount": _f(e.amount), "expense_date": e.expense_date, "payment_method": e.payment_method, "reference_no": e.reference_no,
                 "status": e.status, "source": e.source, "recorded_by_name": names.get(e.recorded_by), "decided_by_name": names.get(e.decided_by), "decided_at": e.decided_at,
                 "decision_note": e.decision_note, "voided_at": e.voided_at, "void_reason": e.void_reason, "created_at": e.created_at, "attachments": atts.get(e.id, [])} for e in rows]

    async def expenses(self, *, status: ExpenseStatus | None, category_id: uuid.UUID | None, vendor_id: uuid.UUID | None, from_date: date | None, to_date: date | None,
                       q: str | None, limit: int, offset: int) -> list[dict]:
        stmt = select(Expense).order_by(Expense.expense_date.desc(), Expense.created_at.desc())
        if status:
            stmt = stmt.where(Expense.status == status)
        if category_id:
            stmt = stmt.where(Expense.category_id == category_id)
        if vendor_id:
            stmt = stmt.where(Expense.vendor_id == vendor_id)
        if from_date:
            stmt = stmt.where(Expense.expense_date >= from_date)
        if to_date:
            stmt = stmt.where(Expense.expense_date <= to_date)
        if q and q.strip():
            stmt = stmt.where(or_(Expense.title.ilike(f"%{q.strip()}%"), Expense.reference_no.ilike(f"%{q.strip()}%"), Expense.description.ilike(f"%{q.strip()}%")))
        rows = list((await self.session.execute(stmt.offset(max(0, offset)).limit(max(1, min(limit, 200))))).scalars())
        return await self._expense_out(rows)

    async def expense_detail(self, expense_id: uuid.UUID) -> dict:
        return (await self._expense_out([await self._expense(expense_id)]))[0]

    async def attachment(self, attachment_id: uuid.UUID) -> ExpenseAttachment:
        a = await self.session.get(ExpenseAttachment, attachment_id)
        if a is None:
            raise ResourceNotFoundError("Attachment not found.")
        return a

    # ------------------------------------------------------------------ other income
    async def create_income(self, *, user_id: uuid.UUID, data: dict) -> IncomeEntry:
        amount = Decimal(str(data["amount"])).quantize(Decimal("0.01"))
        self._check_money(amount, data["income_date"], "income")
        await ensure_open(self.session, data["income_date"], "This income")
        category = await self._category(data["category_id"], EntryKind.INCOME)
        if data["payment_method"] in NEEDS_REFERENCE and not (data.get("reference_no") or "").strip():
            raise BusinessRuleViolationError("Enter the cheque number or the bank transaction reference.")
        i = IncomeEntry(category_id=category.id, received_from=data["received_from"].strip(), amount=amount, income_date=data["income_date"], payment_method=data["payment_method"],
                        reference_no=(data.get("reference_no") or "").strip() or None, description=(data.get("description") or "").strip() or None, recorded_by=user_id)
        self.session.add(i)
        await self.session.flush()
        await self.session.refresh(i)
        await self.audit.log(actor_user_id=user_id, action="accounts.income_recorded", entity_type="income_entry", entity_id=i.id, metadata={"amount": str(amount)})
        return i

    async def void_income(self, income_id: uuid.UUID, *, admin_id: uuid.UUID, reason: str) -> IncomeEntry:
        i = await self.session.get(IncomeEntry, income_id)
        if i is None:
            raise ResourceNotFoundError("Income entry not found.")
        if i.voided_at is not None:
            raise BusinessRuleViolationError("This entry is already voided.")
        await ensure_open(self.session, i.income_date, "This income")
        i.voided_at, i.void_reason = datetime.now(timezone.utc), reason.strip()
        await self.audit.log(actor_user_id=admin_id, action="accounts.income_voided", entity_type="income_entry", entity_id=i.id, metadata={"reason": reason.strip()})
        return i

    async def income_list(self, *, category_id: uuid.UUID | None, from_date: date | None, to_date: date | None, include_voided: bool, q: str | None, limit: int, offset: int) -> list[dict]:
        stmt = select(IncomeEntry).order_by(IncomeEntry.income_date.desc(), IncomeEntry.created_at.desc())
        if category_id:
            stmt = stmt.where(IncomeEntry.category_id == category_id)
        if from_date:
            stmt = stmt.where(IncomeEntry.income_date >= from_date)
        if to_date:
            stmt = stmt.where(IncomeEntry.income_date <= to_date)
        if not include_voided:
            stmt = stmt.where(IncomeEntry.voided_at.is_(None))
        if q and q.strip():
            stmt = stmt.where(or_(IncomeEntry.received_from.ilike(f"%{q.strip()}%"), IncomeEntry.reference_no.ilike(f"%{q.strip()}%"), IncomeEntry.description.ilike(f"%{q.strip()}%")))
        rows = list((await self.session.execute(stmt.offset(max(0, offset)).limit(max(1, min(limit, 200))))).scalars())
        cats = {c.id: c.name for c in (await self.session.execute(select(AccountCategory))).scalars()}
        names = await self._names({r.recorded_by for r in rows})
        return [{"id": i.id, "reference": income_ref(i), "category_id": i.category_id, "category_name": cats.get(i.category_id, "?"), "received_from": i.received_from, "amount": _f(i.amount),
                 "income_date": i.income_date, "payment_method": i.payment_method, "reference_no": i.reference_no, "description": i.description, "recorded_by_name": names.get(i.recorded_by),
                 "voided_at": i.voided_at, "void_reason": i.void_reason, "created_at": i.created_at} for i in rows]

    # ------------------------------------------------------------------ the day book
    async def book(self, *, kind: EntryKind | None, from_date: date | None, to_date: date | None, q: str | None, limit: int, offset: int) -> list[dict]:
        # Each source returns its newest rows first, at most as many as this page can need, so paging
        # through the day book is exact however many years of entries there are (it used to stop at 5,000).
        limit = max(1, min(limit, 500))
        window = max(0, offset) + limit if not (q and q.strip()) else 20000
        rows: list[dict] = []
        cats = {c.id: c.name for c in (await self.session.execute(select(AccountCategory))).scalars()}
        if kind in (None, EntryKind.EXPENSE):
            stmt = select(Expense).where(Expense.status == ExpenseStatus.APPROVED)
            if from_date:
                stmt = stmt.where(Expense.expense_date >= from_date)
            if to_date:
                stmt = stmt.where(Expense.expense_date <= to_date)
            vendors = {v.id: v.name for v in (await self.session.execute(select(Vendor))).scalars()}
            for e in (await self.session.execute(stmt.order_by(Expense.expense_date.desc(), Expense.created_at.desc()).limit(window))).scalars():
                rows.append({"kind": EntryKind.EXPENSE, "id": e.id, "reference": expense_ref(e), "date": e.expense_date, "title": e.title, "party": vendors.get(e.vendor_id), "category": cats.get(e.category_id, "?"),
                             "amount": _f(e.amount), "payment_method": e.payment_method, "status": e.status.value, "bank_reference": e.reference_no, "by": e.recorded_by, "_t": e.created_at})
        if kind in (None, EntryKind.INCOME):
            stmt = select(IncomeEntry).where(IncomeEntry.voided_at.is_(None))
            if from_date:
                stmt = stmt.where(IncomeEntry.income_date >= from_date)
            if to_date:
                stmt = stmt.where(IncomeEntry.income_date <= to_date)
            for i in (await self.session.execute(stmt.order_by(IncomeEntry.income_date.desc(), IncomeEntry.created_at.desc()).limit(window))).scalars():
                rows.append({"kind": EntryKind.INCOME, "id": i.id, "reference": income_ref(i), "date": i.income_date, "title": i.description or i.received_from, "party": i.received_from, "category": cats.get(i.category_id, "?"),
                             "amount": _f(i.amount), "payment_method": i.payment_method, "status": "RECEIVED", "bank_reference": i.reference_no, "by": i.recorded_by, "_t": i.created_at})
        # Student / staff fee payments (money in) and fee refunds paid back (money out) -- the fee ledger.
        fee_types = []
        if kind in (None, EntryKind.INCOME):
            fee_types.append(LedgerEntryType.CREDIT)
        if kind in (None, EntryKind.EXPENSE):
            fee_types.append(LedgerEntryType.REFUND)
        if fee_types:
            from app.fees.models import FeeAssignment, FeeStructure
            from app.students.models import Student

            from app.fees import money as fee_money

            fee_day = fee_money.money_day()
            wanted = FeeLedgerEntry.entry_type.in_(fee_types)
            if LedgerEntryType.CREDIT in fee_types:
                wanted = wanted | fee_money.is_reversal()          # a reversed payment is money back out of fee income
            stmt = (select(FeeLedgerEntry, FeeAssignment.payer_type, FeeAssignment.payer_id, FeeStructure.name)
                    .join(FeeAssignment, FeeAssignment.id == FeeLedgerEntry.fee_assignment_id)
                    .join(FeeStructure, FeeStructure.id == FeeAssignment.fee_structure_id)
                    .where(wanted))
            if from_date:
                stmt = stmt.where(fee_day >= from_date)
            if to_date:
                stmt = stmt.where(fee_day <= to_date)
            fee_rows = (await self.session.execute(stmt.order_by(fee_day.desc(), FeeLedgerEntry.created_at.desc()).limit(window))).all()
            sids = {pid for _e, pt, pid, _n in fee_rows if pt.value == "STUDENT"}
            eids = {pid for _e, pt, pid, _n in fee_rows if pt.value != "STUDENT"}
            names = {}
            if sids:
                names.update({s.id: f"{s.first_name} {s.last_name} ({s.roll_number or '-'})" for s in (await self.session.execute(select(Student).where(Student.id.in_(sids)))).scalars()})
            if eids:
                names.update({e.id: f"{e.first_name} {e.last_name} ({e.employee_code})" for e in (await self.session.execute(select(Employee).where(Employee.id.in_(eids)))).scalars()})
            from app.core.clock import local_date

            for e, _pt, pid, fee_name in fee_rows:
                refund = e.entry_type == LedgerEntryType.REFUND
                reversal = e.entry_type == LedgerEntryType.DEBIT
                pm = e.payment_method.value if hasattr(e.payment_method, "value") else (e.payment_method or "CASH")
                if reversal:
                    rows.append({"kind": EntryKind.INCOME, "id": e.id, "reference": "REVERSAL", "date": local_date(e.created_at),
                                 "title": "Fee payment reversed: " + fee_name, "party": names.get(pid), "category": "Student / staff fees",
                                 "amount": -_f(e.amount), "payment_method": pm, "status": "REVERSED", "bank_reference": None, "by": e.created_by, "_t": e.created_at})
                    continue
                rows.append({"kind": EntryKind.EXPENSE if refund else EntryKind.INCOME, "id": e.id,
                             "reference": e.receipt_number or ("REFUND" if refund else "FEE"), "date": e.paid_on or local_date(e.created_at),
                             "title": ("Fee refund: " if refund else "Fee payment: ") + fee_name, "party": names.get(pid),
                             "category": "Fee refunds" if refund else "Student / staff fees", "amount": _f(e.amount),
                             "payment_method": pm, "status": "PAID" if refund else "RECEIVED", "bank_reference": e.payment_reference, "by": e.created_by, "_t": e.created_at})
        if kind in (None, EntryKind.INCOME):
            # Collected fines are income too (library, attendance, late fee, gate, store...).
            paid_day = func.coalesce(Fine.paid_on, func.date(Fine.resolved_at))
            stmt = select(Fine).where(Fine.status == FineStatus.PAID)
            if from_date:
                stmt = stmt.where(paid_day >= from_date)
            if to_date:
                stmt = stmt.where(paid_day <= to_date)
            fines = list((await self.session.execute(stmt.order_by(paid_day.desc(), Fine.created_at.desc()).limit(window))).scalars())
            if fines:
                from app.fines.service import resolve_payer
                for f in fines:
                    payer = await resolve_payer(self.session, f.payer_type, f.payer_id)
                    day = f.paid_on or (f.resolved_at.date() if f.resolved_at else None)
                    rows.append({"kind": EntryKind.INCOME, "id": f.id, "reference": f.receipt_number or f"FINE-{str(f.id)[:8]}", "date": day,
                                 "title": f"Fine: {f.reason}", "party": payer.name, "category": "Fines \u2014 " + f.source.value.replace("_", " ").title(),
                                 "amount": _f(f.amount), "payment_method": f.payment_method or "CASH", "status": "RECEIVED", "bank_reference": f.payment_reference, "by": f.resolved_by, "_t": f.resolved_at or f.created_at})
        if kind in (None, EntryKind.INCOME):
            # Fine payments that were later reversed: the money came in on its day and went back out on another.
            from app.fines.models import FinePaymentReversal

            stmt = select(FinePaymentReversal, Fine).join(Fine, Fine.id == FinePaymentReversal.fine_id)
            for rv, f in (await self.session.execute(stmt.order_by(FinePaymentReversal.reversed_at.desc()).limit(window))).all():
                from app.fines.service import resolve_payer

                payer = None
                for day, amount, title, ref, status in ((rv.paid_on, _f(rv.amount), f"Fine: {f.reason}", rv.receipt_number or "FINE", "REVERSED"),
                                                        (rv.reversed_on, -_f(rv.amount), f"Fine payment reversed: {rv.reason}", "REVERSAL", "REVERSED")):
                    if (from_date and day < from_date) or (to_date and day > to_date):
                        continue
                    payer = payer or await resolve_payer(self.session, f.payer_type, f.payer_id)
                    rows.append({"kind": EntryKind.INCOME, "id": rv.id, "reference": ref, "date": day, "title": title, "party": payer.name,
                                 "category": "Fines \u2014 " + f.source.value.replace("_", " ").title(), "amount": amount,
                                 "payment_method": rv.payment_method or "CASH", "status": status, "bank_reference": rv.payment_reference, "by": rv.reversed_by, "_t": rv.reversed_at})
        if q and q.strip():
            n = q.strip().lower()
            rows = [r for r in rows if n in r["title"].lower() or n in (r["party"] or "").lower() or n in r["reference"].lower() or n in r["category"].lower()]
        rows.sort(key=lambda r: (r["date"], r["_t"]), reverse=True)
        for r in rows:
            r.pop("_t")
        return rows[max(0, offset): max(0, offset) + max(1, min(limit, 500))]

    # ------------------------------------------------------------------ the report
    async def summary(self, *, from_date: date | None, to_date: date | None) -> dict:
        cats = {c.id: c.name for c in (await self.session.execute(select(AccountCategory))).scalars()}
        # Fee income = payments received minus refunds paid back.
        from app.fees import money as fee_money

        fee_stmt = select(func.coalesce(func.sum(fee_money.signed_amount()), 0)).where(fee_money.moves_money())
        inc_stmt = select(IncomeEntry.category_id, func.sum(IncomeEntry.amount), func.count()).where(IncomeEntry.voided_at.is_(None)).group_by(IncomeEntry.category_id)
        exp_stmt = select(Expense.category_id, func.sum(Expense.amount), func.count()).where(Expense.status == ExpenseStatus.APPROVED).group_by(Expense.category_id)
        pend_stmt = select(func.coalesce(func.sum(Expense.amount), 0), func.count()).where(Expense.status == ExpenseStatus.PENDING_APPROVAL)
        ven_stmt = select(Vendor.name, func.sum(Expense.amount), func.count()).join(Vendor, Vendor.id == Expense.vendor_id).where(Expense.status == ExpenseStatus.APPROVED).group_by(Vendor.name).order_by(func.sum(Expense.amount).desc()).limit(8)
        if from_date:
            fee_stmt = fee_stmt.where(fee_money.money_day() >= from_date)
            inc_stmt, exp_stmt, ven_stmt = (inc_stmt.where(IncomeEntry.income_date >= from_date), exp_stmt.where(Expense.expense_date >= from_date), ven_stmt.where(Expense.expense_date >= from_date))
        if to_date:
            fee_stmt = fee_stmt.where(fee_money.money_day() <= to_date)
            inc_stmt, exp_stmt, ven_stmt = (inc_stmt.where(IncomeEntry.income_date <= to_date), exp_stmt.where(Expense.expense_date <= to_date), ven_stmt.where(Expense.expense_date <= to_date))
        fees = _f((await self.session.execute(fee_stmt)).scalar_one())
        paid_day = func.coalesce(Fine.paid_on, func.date(Fine.resolved_at))
        fine_stmt = select(Fine.source, func.sum(Fine.amount), func.count()).where(Fine.status == FineStatus.PAID).group_by(Fine.source)
        if from_date:
            fine_stmt = fine_stmt.where(paid_day >= from_date)
        if to_date:
            fine_stmt = fine_stmt.where(paid_day <= to_date)
        by_src: dict = {src.value: [_f(a), n] for src, a, n in (await self.session.execute(fine_stmt)).all()}
        from app.fines.models import FinePaymentReversal

        for rv, src in (await self.session.execute(select(FinePaymentReversal, Fine.source).join(Fine, Fine.id == FinePaymentReversal.fine_id))).all():
            for day, amount, count in ((rv.paid_on, _f(rv.amount), 1), (rv.reversed_on, -_f(rv.amount), -1)):
                if (from_date and day < from_date) or (to_date and day > to_date):
                    continue
                cell = by_src.setdefault(src.value, [0.0, 0])
                cell[0], cell[1] = round(cell[0] + amount, 2), cell[1] + count
        fine_rows = [{"source": k, "amount": v[0], "count": v[1]} for k, v in by_src.items() if abs(v[0]) > 0.004 or v[1]]
        fine_rows.sort(key=lambda r: -r["amount"])
        fines_collected = round(sum(r["amount"] for r in fine_rows), 2)
        out_amount, out_count = (await self.session.execute(select(func.coalesce(func.sum(Fine.amount), 0), func.count()).where(Fine.status == FineStatus.PENDING))).one()
        waived_amount = _f((await self.session.execute(select(func.coalesce(func.sum(Fine.amount), 0)).where(Fine.status == FineStatus.WAIVED))).scalar_one())
        inc_rows = [{"category": cats.get(c, "?"), "amount": _f(a), "count": n} for c, a, n in (await self.session.execute(inc_stmt)).all()]
        exp_rows = [{"category": cats.get(c, "?"), "amount": _f(a), "count": n} for c, a, n in (await self.session.execute(exp_stmt)).all()]
        inc_rows.sort(key=lambda r: -r["amount"]); exp_rows.sort(key=lambda r: -r["amount"])
        other, spent = round(sum(r["amount"] for r in inc_rows), 2), round(sum(r["amount"] for r in exp_rows), 2)
        pending_amount, pending_count = (await self.session.execute(pend_stmt)).one()
        # twelve months ending at the end of the period (or today)
        end = to_date or institute_today()
        months = []
        y, m = end.year, end.month
        for _ in range(12):
            months.append(f"{y:04d}-{m:02d}")
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        months.reverse()
        first = date(int(months[0][:4]), int(months[0][5:]), 1)
        month_of = lambda col: func.to_char(col, "YYYY-MM")                                               # noqa: E731
        fm = month_of(fee_money.money_day()).label("m")
        fee_m = {mo: _f(a) for mo, a in (await self.session.execute(select(fm, func.sum(fee_money.signed_amount())).where(fee_money.moves_money(), fee_money.money_day() >= first).group_by(fm))).all()}
        im = month_of(IncomeEntry.income_date).label("m")
        inc_m = {mo: _f(a) for mo, a in (await self.session.execute(select(im, func.sum(IncomeEntry.amount)).where(IncomeEntry.voided_at.is_(None), IncomeEntry.income_date >= first).group_by(im))).all()}
        em = month_of(Expense.expense_date).label("m")
        fnm = func.to_char(paid_day, "YYYY-MM").label("m")
        fine_m = {mo: _f(a) for mo, a in (await self.session.execute(select(fnm, func.sum(Fine.amount)).where(Fine.status == FineStatus.PAID, paid_day >= first).group_by(fnm))).all()}
        for rv in (await self.session.execute(select(FinePaymentReversal))).scalars():
            for day, amount in ((rv.paid_on, _f(rv.amount)), (rv.reversed_on, -_f(rv.amount))):
                if day and day >= first:
                    key = f"{day.year:04d}-{day.month:02d}"
                    fine_m[key] = round(fine_m.get(key, 0.0) + amount, 2)
        exp_m = {mo: _f(a) for mo, a in (await self.session.execute(select(em, func.sum(Expense.amount)).where(Expense.status == ExpenseStatus.APPROVED, Expense.expense_date >= first).group_by(em))).all()}
        monthly = [{"month": mo, "fees": fee_m.get(mo, 0.0), "fines": fine_m.get(mo, 0.0), "other_income": inc_m.get(mo, 0.0),
                    "income": round(fee_m.get(mo, 0.0) + fine_m.get(mo, 0.0) + inc_m.get(mo, 0.0), 2), "expenses": exp_m.get(mo, 0.0)} for mo in months]
        total_income = round(fees + fines_collected + other, 2)
        return {"from_date": from_date, "to_date": to_date, "fees_collected": fees, "fines_collected": fines_collected, "fines_by_source": fine_rows,
                "fines_outstanding": {"count": out_count, "amount": _f(out_amount)}, "fines_waived": waived_amount,
                "other_income": other, "total_income": total_income, "expenses": spent, "net": round(total_income - spent, 2),
                "income_by_category": inc_rows, "expenses_by_category": exp_rows, "pending_expenses": {"count": pending_count, "amount": _f(pending_amount)}, "monthly": monthly,
                "top_vendors": [{"vendor": n, "amount": _f(a), "count": c} for n, a, c in (await self.session.execute(ven_stmt)).all()]}