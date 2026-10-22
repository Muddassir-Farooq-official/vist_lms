from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import AccountCategory, EntryKind, Expense, ExpenseAttachment, ExpenseSource, ExpenseStatus, PaymentMethod, Vendor
from app.accounts.service import NEEDS_REFERENCE, AccountsService, expense_ref
from app.audit.service import AuditService
from app.auth.models import Role, UserRole
from app.auth.permissions import Roles
from app.core.clock import institute_today
from app.core.config import settings
from app.core.exceptions import AuthorizationError, BusinessRuleViolationError, ResourceNotFoundError
from app.core.storage import FileTooLargeError, UnsupportedFileTypeError, delete_file, save_attachment, validate_attachment
from app.notifications.models import NotificationCategory
from app.notifications.service import create_notification
from app.purchasing.models import PurchaseItem, PurchaseOrder, PurchaseStatus

MAX_TOTAL = Decimal("100000000")
MAX_FILES = 5
S = PurchaseStatus


def po_ref(o: PurchaseOrder) -> str:
    return f"PO-{o.created_at.year}-{o.serial_no:05d}"


def _q(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _f(v) -> float | None:
    return None if v is None else round(float(v), 2)


class PurchasingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)
        self.accounts = AccountsService(session)

    # ------------------------------------------------------------------ helpers
    async def _order(self, order_id: uuid.UUID) -> PurchaseOrder:
        o = await self.session.get(PurchaseOrder, order_id)
        if o is None:
            raise ResourceNotFoundError("Purchase request not found.")
        return o

    async def _link_for(self, user_id: uuid.UUID | None) -> str:
        if user_id is None:
            return "/dashboard"
        codes = set((await self.session.execute(select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id))).scalars())
        for role, link in ((Roles.ADMIN, "/dashboard/admin/finance?open=purchases"), (Roles.ACCOUNTS_OFFICER, "/dashboard/accounts/finance?open=purchases"), (Roles.TEACHER, "/dashboard/faculty/purchases"),
                           (Roles.LIBRARIAN, "/dashboard/library/purchases"), (Roles.SECURITY_GUARD, "/dashboard/gate/purchases"), (Roles.EXAM_CONTROLLER, "/dashboard/exam/purchases"), (Roles.HOSTEL_WARDEN, "/dashboard/warden/purchases")):
            if role in codes:
                return link
        return "/dashboard"

    async def _tell(self, user_id: uuid.UUID | None, title: str, message: str) -> None:
        if user_id:
            await create_notification(self.session, user_id=user_id, category=NotificationCategory.FINANCE, title=title, message=message, link=await self._link_for(user_id))

    @staticmethod
    def needs_price_approval(o: PurchaseOrder) -> bool:
        """Goods that cost well over the approved estimate wait for the administration to approve the higher price before they can be paid."""
        if o.actual_total is None or o.price_approved_at is not None:
            return False
        limit = Decimal(str(o.estimated_total)) * (Decimal(1) + Decimal(str(settings.purchase_over_estimate_percent)) / Decimal(100))
        return Decimal(str(o.actual_total)) > limit

    @staticmethod
    def _actions(o: PurchaseOrder, *, is_owner: bool, can_approve: bool, can_record: bool) -> list[str]:
        a: list[str] = []
        if o.status == S.REQUESTED:
            if is_owner or can_approve:
                a.append("cancel")
            if can_approve:
                a += ["approve", "reject"]
        elif o.status == S.APPROVED:
            a += (["order"] if can_record else []) + (["cancel"] if can_approve else [])
        elif o.status == S.ORDERED:
            a += (["receive"] if can_record else []) + (["cancel"] if can_approve else [])
        elif o.status == S.RECEIVED:
            if PurchasingService.needs_price_approval(o):
                a += ["approve_price"] if can_approve else []
            else:
                a += ["pay"] if can_record else []
        elif o.status == S.PAID:
            a += ["reverse"] if can_approve else []
        return a

    # ------------------------------------------------------------------ asking for something
    async def create(self, *, user_id: uuid.UUID, data: dict) -> PurchaseOrder:
        if data.get("needed_by") and data["needed_by"] < institute_today():
            raise BusinessRuleViolationError("The date you need it by cannot be in the past.")
        if data.get("category_id"):
            c = await self.session.get(AccountCategory, data["category_id"])
            if c is None or c.kind != EntryKind.EXPENSE or not c.is_active or c.is_system:
                raise BusinessRuleViolationError("Choose an active expense category, or leave it empty.")
        if data.get("suggested_vendor_id"):
            v = await self.session.get(Vendor, data["suggested_vendor_id"])
            if v is None or not v.is_active:
                raise BusinessRuleViolationError("Choose an active vendor, or leave it empty.")
        total = sum(_q(i["quantity"]) * _q(i["estimated_unit_price"]) for i in data["items"])
        if total > MAX_TOTAL:
            raise BusinessRuleViolationError("The total is more than Rs. 100,000,000. Split it into smaller requests.")
        o = PurchaseOrder(requested_by=user_id, title=data["title"].strip(), justification=data["justification"].strip(), needed_by=data.get("needed_by"), category_id=data.get("category_id"),
                          suggested_vendor_id=data.get("suggested_vendor_id"), status=S.REQUESTED, estimated_total=_q(total))
        self.session.add(o)
        await self.session.flush()
        await self.session.refresh(o)
        for n, i in enumerate(data["items"]):
            self.session.add(PurchaseItem(order_id=o.id, position=n, description=i["description"].strip(), quantity=_q(i["quantity"]), estimated_unit_price=_q(i["estimated_unit_price"])))
        await self.audit.log(actor_user_id=user_id, action="purchase.requested", entity_type="purchase_order", entity_id=o.id, metadata={"estimated_total": str(total)})
        who = (await self.accounts._names({user_id})).get(user_id, "Someone")
        for admin in await self.accounts._admin_ids():
            if admin != user_id:
                await self._tell(admin, f"Purchase request waiting: Rs. {float(total):,.0f}", f"{who}: {o.title}")
        return o

    async def cancel(self, order_id: uuid.UUID, *, actor_id: uuid.UUID, is_admin: bool, reason: str | None) -> PurchaseOrder:
        o = await self._order(order_id)
        if o.status == S.REQUESTED and (o.requested_by == actor_id or is_admin):
            pass
        elif is_admin and o.status in (S.APPROVED, S.ORDERED):
            pass
        elif o.status == S.REQUESTED:
            raise AuthorizationError("Only the person who asked, or the administration, can cancel a request.")
        else:
            raise BusinessRuleViolationError("It can no longer be cancelled at this stage." if o.status in (S.RECEIVED, S.PAID) else "This request is already closed.")
        if o.requested_by != actor_id and len((reason or "").strip()) < 3:
            raise BusinessRuleViolationError("Say why you are cancelling it.")
        o.status, o.cancelled_by, o.cancelled_at, o.cancel_reason = S.CANCELLED, actor_id, datetime.now(timezone.utc), (reason or "").strip() or None
        await self.audit.log(actor_user_id=actor_id, action="purchase.cancelled", entity_type="purchase_order", entity_id=o.id)
        if o.requested_by != actor_id:
            await self._tell(o.requested_by, f"Purchase cancelled: {po_ref(o)}", f"{o.title}" + (f" \u2014 {o.cancel_reason}" if o.cancel_reason else ""))
        return o

    # ------------------------------------------------------------------ the administration decides
    async def decide(self, order_id: uuid.UUID, *, admin_id: uuid.UUID, approve: bool, note: str | None) -> PurchaseOrder:
        o = await self._order(order_id)
        if o.status != S.REQUESTED:
            raise BusinessRuleViolationError("This request is not waiting for a decision.")
        if not approve and len((note or "").strip()) < 3:
            raise BusinessRuleViolationError("Say why you are rejecting it.")
        o.status = S.APPROVED if approve else S.REJECTED
        o.decided_by, o.decided_at, o.decision_note = admin_id, datetime.now(timezone.utc), (note or "").strip() or None
        await self.audit.log(actor_user_id=admin_id, action="purchase.approved" if approve else "purchase.rejected", entity_type="purchase_order", entity_id=o.id)
        if o.requested_by != admin_id:
            await self._tell(o.requested_by, f"Purchase {'approved' if approve else 'rejected'}: {po_ref(o)}", o.title + (f" \u2014 {o.decision_note}" if o.decision_note else ("" if not approve else " \u2014 the accounts office will order it.")))
        if approve:                                              # the accounts office has something to order
            officers = set((await self.session.execute(select(UserRole.user_id).join(Role, Role.id == UserRole.role_id).where(Role.code == Roles.ACCOUNTS_OFFICER))).scalars())
            for user in officers - {o.requested_by, admin_id}:
                await self._tell(user, f"Approved, ready to order: {po_ref(o)}", f"{o.title} (about Rs. {float(o.estimated_total):,.0f})")
        return o

    # ------------------------------------------------------------------ the accounts office buys, receives and pays
    async def place_order(self, order_id: uuid.UUID, *, actor_id: uuid.UUID, vendor_id: uuid.UUID, expected_delivery: date | None, note: str | None) -> PurchaseOrder:
        o = await self._order(order_id)
        if o.status != S.APPROVED:
            raise BusinessRuleViolationError("Only an approved request can be ordered.")
        v = await self.session.get(Vendor, vendor_id)
        if v is None or not v.is_active:
            raise BusinessRuleViolationError("Choose an active vendor.")
        today = institute_today()
        if expected_delivery and expected_delivery < today:
            raise BusinessRuleViolationError("The expected delivery date cannot be in the past.")
        o.status, o.vendor_id, o.ordered_by, o.ordered_at, o.expected_delivery, o.order_note = S.ORDERED, v.id, actor_id, datetime.now(timezone.utc), expected_delivery, (note or "").strip() or None
        await self.audit.log(actor_user_id=actor_id, action="purchase.ordered", entity_type="purchase_order", entity_id=o.id, metadata={"vendor": v.name})
        await self._tell(o.requested_by, f"Ordered: {po_ref(o)}", f"{o.title} has been ordered from {v.name}" + (f"; expected {expected_delivery:%d %b}." if expected_delivery else "."))
        return o

    async def receive(self, order_id: uuid.UUID, *, actor_id: uuid.UUID, invoice_no: str, lines: list[dict]) -> PurchaseOrder:
        o = await self._order(order_id)
        if o.status != S.ORDERED:
            raise BusinessRuleViolationError("Only an order that has been placed can be received.")
        items = {i.id: i for i in (await self.session.execute(select(PurchaseItem).where(PurchaseItem.order_id == o.id))).scalars()}
        seen: set = set()
        total = Decimal(0)
        for ln in lines:
            item = items.get(ln["item_id"])
            if item is None:
                raise BusinessRuleViolationError("One of the lines is not part of this order.")
            if ln["item_id"] in seen:
                raise BusinessRuleViolationError("An item appears twice.")
            seen.add(ln["item_id"])
            qty, price = _q(ln["received_quantity"]), _q(ln["actual_unit_price"])
            if qty > _q(item.quantity):
                raise BusinessRuleViolationError(f"\u201c{item.description}\u201d: you cannot receive more than the {float(item.quantity):g} ordered.")
            if qty > 0 and price <= 0:
                raise BusinessRuleViolationError(f"\u201c{item.description}\u201d: enter the price actually charged.")
            item.received_quantity, item.actual_unit_price = qty, price
            total += qty * price
        for item in items.values():
            if item.id not in seen:                       # anything not listed did not arrive
                item.received_quantity, item.actual_unit_price = Decimal(0), _q(item.estimated_unit_price)
        total = _q(total)
        invoice = (invoice_no or "").strip()
        if o.vendor_id and invoice:
            twin = (await self.session.execute(select(PurchaseOrder).where(
                PurchaseOrder.vendor_id == o.vendor_id, PurchaseOrder.id != o.id, PurchaseOrder.invoice_no.is_not(None),
                func.lower(func.trim(PurchaseOrder.invoice_no)) == invoice.lower(), PurchaseOrder.status != S.CANCELLED))).scalars().first()
            if twin is not None:
                raise BusinessRuleViolationError(f"Invoice number {invoice} from this vendor is already recorded on {po_ref(twin)}. The same bill cannot be paid twice.")
        if total <= 0:
            raise BusinessRuleViolationError("Nothing was received. If the order did not arrive, cancel it instead.")
        if total > MAX_TOTAL:
            raise BusinessRuleViolationError("The total is more than Rs. 100,000,000. Check the prices.")
        o.status, o.received_by, o.received_at, o.invoice_no, o.actual_total = S.RECEIVED, actor_id, datetime.now(timezone.utc), invoice_no.strip(), total
        await self.audit.log(actor_user_id=actor_id, action="purchase.received", entity_type="purchase_order", entity_id=o.id, metadata={"actual_total": str(total)})
        await self._tell(o.requested_by, f"Arrived: {po_ref(o)}", f"{o.title} has been received. Collect it from the accounts office.")
        return o

    async def approve_price(self, order_id: uuid.UUID, *, admin_id: uuid.UUID) -> PurchaseOrder:
        o = await self._order(order_id)
        if o.status != S.RECEIVED or not self.needs_price_approval(o):
            raise BusinessRuleViolationError("This purchase does not need its price approved.")
        o.price_approved_by, o.price_approved_at = admin_id, datetime.now(timezone.utc)
        await self.audit.log(actor_user_id=admin_id, action="purchase.price_approved", entity_type="purchase_order", entity_id=o.id,
                             metadata={"estimated_total": str(o.estimated_total), "actual_total": str(o.actual_total)})
        return o

    async def pay(self, order_id: uuid.UUID, *, actor_id: uuid.UUID, method: PaymentMethod, reference: str | None, paid_date: date, files: list[tuple[str, bytes, str | None]]) -> PurchaseOrder:
        o = await self._order(order_id)
        if o.status != S.RECEIVED:
            raise BusinessRuleViolationError("Only goods that have been received can be paid for.")
        if self.needs_price_approval(o):
            raise BusinessRuleViolationError(
                f"The goods cost Rs. {float(o.actual_total):,.0f} against an approved Rs. {float(o.estimated_total):,.0f}. An administrator must approve the price before it can be paid.")
        if method in NEEDS_REFERENCE and not (reference or "").strip():
            raise BusinessRuleViolationError("Enter the cheque number or the bank transaction reference.")
        today = institute_today()
        if paid_date > today:
            raise BusinessRuleViolationError("The payment date cannot be in the future.")
        if len(files) > MAX_FILES:
            raise BusinessRuleViolationError(f"You can attach at most {MAX_FILES} files.")
        for name, content, ctype in files:
            try:
                validate_attachment(content=content, original_filename=name, declared_content_type=ctype)
            except (UnsupportedFileTypeError, FileTooLargeError) as exc:
                raise BusinessRuleViolationError(f"\u201c{name}\u201d: {exc}") from exc
        await self.accounts.categories(None, False)                                                    # makes sure the standard categories exist
        cat = await self.session.get(AccountCategory, o.category_id) if o.category_id else None
        if cat is None or not cat.is_active:
            cat = (await self.session.execute(select(AccountCategory).where(AccountCategory.kind == EntryKind.EXPENSE, AccountCategory.name == "Purchases"))).scalars().first()
        vendor = await self.session.get(Vendor, o.vendor_id) if o.vendor_id else None
        e = Expense(category_id=cat.id, vendor_id=o.vendor_id, title=f"{o.title} ({po_ref(o)})", description=f"Invoice {o.invoice_no} from {vendor.name if vendor else 'the vendor'}.", amount=o.actual_total,
                    expense_date=paid_date, payment_method=method, reference_no=(reference or "").strip() or None, status=ExpenseStatus.APPROVED, source=ExpenseSource.PURCHASE, source_ref_id=o.id,
                    recorded_by=actor_id, decided_by=o.decided_by, decided_at=o.decided_at, decision_note="Approved as a purchase request.")
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
        o.status, o.paid_by, o.paid_at, o.payment_method, o.payment_reference, o.expense_id = S.PAID, actor_id, datetime.now(timezone.utc), method, (reference or "").strip() or None, e.id
        await self.audit.log(actor_user_id=actor_id, action="purchase.paid", entity_type="purchase_order", entity_id=o.id, metadata={"amount": str(o.actual_total), "expense": str(e.id)})
        return o

    async def reverse_payment(self, order_id: uuid.UUID, *, admin_id: uuid.UUID, reason: str) -> PurchaseOrder:
        o = await self._order(order_id)
        if o.status != S.PAID:
            raise BusinessRuleViolationError("Only a paid purchase can have its payment reversed.")
        e = await self.session.get(Expense, o.expense_id) if o.expense_id else None
        if e is not None:
            e.status, e.voided_at, e.void_reason = ExpenseStatus.VOIDED, datetime.now(timezone.utc), f"Payment reversed: {reason.strip()}"
        o.status, o.paid_by, o.paid_at, o.payment_method, o.payment_reference, o.expense_id = S.RECEIVED, None, None, None, None, None
        await self.audit.log(actor_user_id=admin_id, action="purchase.payment_reversed", entity_type="purchase_order", entity_id=o.id, metadata={"reason": reason.strip()})
        return o

    # ------------------------------------------------------------------ reading
    async def _out(self, orders: list[PurchaseOrder], *, viewer_id: uuid.UUID, can_approve: bool, can_record: bool) -> list[dict]:
        if not orders:
            return []
        ids = [o.id for o in orders]
        items: dict = {}
        for it in (await self.session.execute(select(PurchaseItem).where(PurchaseItem.order_id.in_(ids)).order_by(PurchaseItem.position))).scalars():
            rq, ap = (float(it.received_quantity) if it.received_quantity is not None else None), (float(it.actual_unit_price) if it.actual_unit_price is not None else None)
            items.setdefault(it.order_id, []).append({"id": it.id, "description": it.description, "quantity": float(it.quantity), "estimated_unit_price": float(it.estimated_unit_price),
                                                      "estimated_total": round(float(it.quantity) * float(it.estimated_unit_price), 2), "received_quantity": rq, "actual_unit_price": ap,
                                                      "actual_total": round(rq * ap, 2) if rq is not None and ap is not None else None})
        cats = {c.id: c.name for c in (await self.session.execute(select(AccountCategory))).scalars()}
        vendors = {v.id: v.name for v in (await self.session.execute(select(Vendor))).scalars()}
        names = await self.accounts._names({o.requested_by for o in orders} | {o.decided_by for o in orders})
        exps = {e.id: expense_ref(e) for e in (await self.session.execute(select(Expense).where(Expense.id.in_([o.expense_id for o in orders if o.expense_id])))).scalars()} if any(o.expense_id for o in orders) else {}
        out = []
        for o in orders:
            over = round((float(o.actual_total) - float(o.estimated_total)) / float(o.estimated_total) * 100, 1) if o.actual_total is not None and float(o.estimated_total) > 0 else None
            out.append({"id": o.id, "reference": po_ref(o), "title": o.title, "justification": o.justification, "needed_by": o.needed_by, "status": o.status, "requested_by_name": names.get(o.requested_by),
                        "category_id": o.category_id, "category_name": cats.get(o.category_id), "suggested_vendor_name": vendors.get(o.suggested_vendor_id), "vendor_id": o.vendor_id, "vendor_name": vendors.get(o.vendor_id),
                        "estimated_total": _f(o.estimated_total), "actual_total": _f(o.actual_total), "over_estimate_percent": over, "items": items.get(o.id, []), "created_at": o.created_at,
                        "decided_by_name": names.get(o.decided_by), "decided_at": o.decided_at, "decision_note": o.decision_note, "ordered_at": o.ordered_at, "expected_delivery": o.expected_delivery,
                        "order_note": o.order_note, "received_at": o.received_at, "invoice_no": o.invoice_no, "paid_at": o.paid_at, "payment_method": o.payment_method, "payment_reference": o.payment_reference,
                        "expense_id": o.expense_id, "expense_reference": exps.get(o.expense_id), "cancelled_at": o.cancelled_at, "cancel_reason": o.cancel_reason,
                        "actions": self._actions(o, is_owner=o.requested_by == viewer_id, can_approve=can_approve, can_record=can_record)})
        return out

    async def listing(self, *, viewer_id: uuid.UUID, can_approve: bool, can_record: bool, status: PurchaseStatus | None, q: str | None, mine_only: bool, limit: int, offset: int) -> list[dict]:
        stmt = select(PurchaseOrder).order_by(PurchaseOrder.created_at.desc())
        if mine_only:
            stmt = stmt.where(PurchaseOrder.requested_by == viewer_id)
        if status:
            stmt = stmt.where(PurchaseOrder.status == status)
        if q and q.strip():
            stmt = stmt.where(or_(PurchaseOrder.title.ilike(f"%{q.strip()}%"), PurchaseOrder.invoice_no.ilike(f"%{q.strip()}%")))
        rows = list((await self.session.execute(stmt.offset(max(0, offset)).limit(max(1, min(limit, 200))))).scalars())
        return await self._out(rows, viewer_id=viewer_id, can_approve=can_approve, can_record=can_record)

    async def detail(self, order_id: uuid.UUID, *, viewer_id: uuid.UUID, can_approve: bool, can_record: bool, can_see_all: bool) -> dict:
        o = await self._order(order_id)
        if o.requested_by != viewer_id and not can_see_all:
            raise ResourceNotFoundError("Purchase request not found.")
        return (await self._out([o], viewer_id=viewer_id, can_approve=can_approve, can_record=can_record))[0]

    async def summary(self) -> dict:
        count = lambda st: select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.status == st)         # noqa: E731
        c = {st: (await self.session.execute(count(st))).scalar_one() for st in (S.REQUESTED, S.APPROVED, S.ORDERED, S.RECEIVED)}
        owed = (await self.session.execute(select(func.coalesce(func.sum(PurchaseOrder.actual_total), 0)).where(PurchaseOrder.status == S.RECEIVED))).scalar_one()
        paid = (await self.session.execute(select(func.coalesce(func.sum(PurchaseOrder.actual_total), 0)).where(PurchaseOrder.status == S.PAID))).scalar_one()
        return {"waiting_for_approval": c[S.REQUESTED], "to_order": c[S.APPROVED], "on_order": c[S.ORDERED], "to_pay": c[S.RECEIVED], "owed_to_vendors": _f(owed), "paid_total": _f(paid)}