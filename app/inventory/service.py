"""Inventory: stock in (opening, purchases), stock out (issues, free or paid), returns, adjustments and reversals.

Every change goes through _move(), which locks the item row, refuses to take stock below zero, keeps a moving
average cost, writes the append-only StockMovement row and an audit log entry. Money is never kept here:
a paid issue becomes an income entry in Accounts (with a receipt), a "pay later" issue becomes a fine.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import AuditService
from app.core.clock import institute_today
from app.core.exceptions import BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError
from app.core.sequences import next_sequence_value
from app.inventory.models import ChargeMode, MovementKind, StockItem, StockMovement

PARTY_TYPES = {"STUDENT", "EMPLOYEE", "DEPARTMENT"}
PAY_METHODS = {"CASH", "BANK_TRANSFER", "CHEQUE", "ONLINE"}
STORE_SALES_CATEGORY = "Store sales"
STORE_REFUNDS_CATEGORY = "Store refunds"


def _d(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _f(v) -> float:
    return float(v or 0)


def movement_ref(m: StockMovement) -> str:
    return f"MOV-{m.serial_no:06d}"


class InventoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ------------------------------------------------------------------ items
    async def _item(self, item_id: uuid.UUID, *, lock: bool = False) -> StockItem:
        stmt = select(StockItem).where(StockItem.id == item_id)
        if lock:
            stmt = stmt.with_for_update()
        item = (await self.session.execute(stmt)).scalar_one_or_none()
        if item is None:
            raise ResourceNotFoundError("Stock item not found.")
        return item

    async def create_item(self, data: dict, *, actor_id: uuid.UUID) -> StockItem:
        name = " ".join((data.get("name") or "").split())
        if not name:
            raise BusinessRuleViolationError("Give the item a name.")
        code = " ".join((data.get("code") or "").split()).upper()
        if not code:
            code = f"STK-{await next_sequence_value(self.session, 'stock_item_code'):04d}"
        if (await self.session.execute(select(StockItem).where(func.upper(StockItem.code) == code))).scalar_one_or_none():
            raise DuplicateResourceError(f"An item with code {code} already exists.")
        dup = (await self.session.execute(select(StockItem).where(func.lower(StockItem.name) == name.lower()))).scalar_one_or_none()
        if dup is not None:
            raise DuplicateResourceError(f"An item called '{dup.name}' already exists ({dup.code}).")
        item = StockItem(code=code, name=name, category=(data.get("category") or "General").strip() or "General",
                         unit=(data.get("unit") or "pcs").strip() or "pcs", sale_price=data.get("sale_price"),
                         reorder_level=_d(data.get("reorder_level")), is_returnable=bool(data.get("is_returnable")),
                         quantity_on_hand=Decimal("0"), average_cost=Decimal("0"), created_by=actor_id)
        self.session.add(item)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="inventory.item_created", entity_type="stock_item", entity_id=item.id,
                             after={"code": code, "name": name})
        opening = _d(data.get("opening_quantity"))
        if opening > 0:
            await self._move(item, MovementKind.OPENING, opening, unit_cost=_d(data.get("opening_unit_cost")), actor_id=actor_id,
                             reason="Opening stock")
        return item

    async def update_item(self, item_id: uuid.UUID, changes: dict, *, actor_id: uuid.UUID) -> StockItem:
        item = await self._item(item_id)
        before = {k: str(getattr(item, k)) for k in changes}
        if "name" in changes and changes["name"] is not None:
            name = " ".join(changes["name"].split())
            if not name:
                raise BusinessRuleViolationError("The name cannot be empty.")
            dup = (await self.session.execute(select(StockItem).where(func.lower(StockItem.name) == name.lower(), StockItem.id != item.id))).scalar_one_or_none()
            if dup is not None:
                raise DuplicateResourceError(f"An item called '{dup.name}' already exists ({dup.code}).")
            item.name = name
        for k in ("category", "unit"):
            if changes.get(k):
                setattr(item, k, changes[k].strip())
        if "sale_price" in changes:
            item.sale_price = changes["sale_price"]
        if changes.get("reorder_level") is not None:
            item.reorder_level = _d(changes["reorder_level"])
        for k in ("is_returnable", "is_active"):
            if changes.get(k) is not None:
                setattr(item, k, bool(changes[k]))
        await self.audit.log(actor_user_id=actor_id, action="inventory.item_updated", entity_type="stock_item", entity_id=item.id,
                             before=before, after={k: str(getattr(item, k)) for k in changes})
        return item

    # ------------------------------------------------------------------ the one place stock changes
    async def _move(self, item: StockItem, kind: MovementKind, qty: Decimal, *, unit_cost: Decimal | None = None, actor_id: uuid.UUID,
                    reason: str | None = None, **fields) -> StockMovement:
        """qty is always positive here; the sign comes from the kind (or from `outgoing` for ADJUSTED/REVERSED)."""
        outgoing = fields.pop("outgoing", None)
        if outgoing is None:
            outgoing = kind == MovementKind.ISSUED
        item = await self._item(item.id, lock=True)
        on_hand, avg = _d(item.quantity_on_hand), _d(item.average_cost)
        if qty <= 0:
            raise BusinessRuleViolationError("The quantity must be more than 0.")
        if outgoing:
            if qty > on_hand:
                raise BusinessRuleViolationError(f"Only {on_hand.normalize():f} {item.unit} of {item.name} in stock.")
            cost = avg if unit_cost is None else unit_cost
            new_qty = on_hand - qty
            if kind == MovementKind.REVERSED and new_qty > 0:          # undoing an incoming row takes its value back out
                avg = max(_d((on_hand * _d(item.average_cost) - qty * cost) / new_qty), Decimal("0"))
            signed = -qty
        else:
            cost = avg if unit_cost is None or unit_cost <= 0 else unit_cost
            new_qty = on_hand + qty
            avg = _d((on_hand * avg + qty * cost) / new_qty) if new_qty > 0 else cost
            signed = qty
        item.quantity_on_hand, item.average_cost = new_qty, avg
        m = StockMovement(item_id=item.id, kind=kind, quantity=signed, unit_cost=_d(cost), balance_after=new_qty, reason=reason,
                          recorded_by=actor_id, **fields)
        self.session.add(m)
        await self.session.flush()
        await self.session.refresh(m)
        await self.audit.log(actor_user_id=actor_id, action=f"inventory.{kind.value.lower()}", entity_type="stock_movement", entity_id=m.id,
                             metadata={"item": item.code, "quantity": str(signed), "unit_cost": str(_d(cost)), "balance_after": str(new_qty),
                                       "party": fields.get("party_name"), "reason": reason})
        return m

    # ------------------------------------------------------------------ in: from purchases
    async def _stocked(self, purchase_item_id: uuid.UUID) -> Decimal:
        """How much of a purchase-order line has been taken in: put into the store, or added to the fixed-asset register
        (one asset per unit). Counting both stops the same goods being taken in twice."""
        from app.assets.models import Asset

        total = (await self.session.execute(select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(StockMovement.purchase_item_id == purchase_item_id))).scalar_one()
        assets = (await self.session.execute(select(func.count()).select_from(Asset).where(Asset.purchase_item_id == purchase_item_id))).scalar_one()
        return _d(total) + _d(assets)

    async def purchases_to_stock(self) -> list[dict]:
        """Received purchase orders with lines not yet (fully) put into stock."""
        from app.purchasing.models import PurchaseItem, PurchaseOrder, PurchaseStatus

        orders = (await self.session.execute(select(PurchaseOrder).where(PurchaseOrder.status.in_([PurchaseStatus.RECEIVED, PurchaseStatus.PAID]))
                                             .order_by(PurchaseOrder.received_at.desc().nullslast()).limit(200))).scalars().all()
        out = []
        for o in orders:
            lines = []
            for pi in (await self.session.execute(select(PurchaseItem).where(PurchaseItem.order_id == o.id).order_by(PurchaseItem.position))).scalars():
                got = _d(pi.received_quantity if pi.received_quantity is not None else pi.quantity)
                left = got - await self._stocked(pi.id)
                if left > 0:
                    lines.append({"purchase_item_id": pi.id, "description": pi.description, "received": _f(got), "left_to_stock": _f(left),
                                  "unit_cost": _f(pi.actual_unit_price if pi.actual_unit_price is not None else pi.estimated_unit_price)})
            if lines:
                out.append({"order_id": o.id, "reference": f"PO-{o.created_at.year}-{o.serial_no:05d}", "title": o.title, "status": o.status.value,
                            "invoice_no": o.invoice_no, "lines": lines})
        return out

    async def receive_from_purchase(self, order_id: uuid.UUID, lines: list[dict], *, actor_id: uuid.UUID) -> list[StockMovement]:
        from app.purchasing.models import PurchaseItem, PurchaseOrder, PurchaseStatus

        order = await self.session.get(PurchaseOrder, order_id)
        if order is None:
            raise ResourceNotFoundError("Purchase order not found.")
        if order.status not in (PurchaseStatus.RECEIVED, PurchaseStatus.PAID):
            raise BusinessRuleViolationError("Only goods that have been received (or paid for) can be put into stock.")
        if not lines:
            raise BusinessRuleViolationError("Choose at least one line to put into stock.")
        ref = f"PO-{order.created_at.year}-{order.serial_no:05d}"
        made = []
        for line in lines:
            pi = await self.session.get(PurchaseItem, line["purchase_item_id"])
            if pi is None or pi.order_id != order.id:
                raise BusinessRuleViolationError("That line does not belong to this purchase order.")
            got = _d(pi.received_quantity if pi.received_quantity is not None else pi.quantity)
            left = got - await self._stocked(pi.id)
            qty = _d(line.get("quantity") or left)
            if qty > left:
                raise BusinessRuleViolationError(f"'{pi.description}': only {left.normalize():f} left to put into stock.")
            if line.get("stock_item_id"):
                item = await self._item(line["stock_item_id"])
            else:
                new = line.get("new_item") or {}
                item = await self.create_item({"name": new.get("name") or pi.description, "category": new.get("category"), "unit": new.get("unit"),
                                               "sale_price": new.get("sale_price"), "is_returnable": new.get("is_returnable"),
                                               "reorder_level": new.get("reorder_level")}, actor_id=actor_id)
            cost = _d(pi.actual_unit_price if pi.actual_unit_price is not None else pi.estimated_unit_price)
            made.append(await self._move(item, MovementKind.RECEIVED, qty, unit_cost=cost, actor_id=actor_id, reason=f"From {ref}: {pi.description}",
                                         purchase_order_id=order.id, purchase_item_id=pi.id))
        return made

    # ------------------------------------------------------------------ out: issue to someone
    async def _party(self, party_type: str, party_id: uuid.UUID | None, department: str | None) -> tuple[str, uuid.UUID | None, str]:
        from app.fines.models import FinePayerType
        from app.fines.service import payer_exists, resolve_payer

        party_type = (party_type or "").upper()
        if party_type not in PARTY_TYPES:
            raise BusinessRuleViolationError("Issue to a student, a staff member or a department.")
        if party_type == "DEPARTMENT":
            name = " ".join((department or "").split())
            if not name:
                raise BusinessRuleViolationError("Name the department (e.g. Computer Lab, Library, Admin office).")
            return party_type, None, name
        if party_id is None:
            raise BusinessRuleViolationError("Choose the person.")
        pt = FinePayerType(party_type)
        if not await payer_exists(self.session, pt, party_id):
            raise ResourceNotFoundError(f"{party_type.title()} not found.")
        p = await resolve_payer(self.session, pt, party_id)
        return party_type, party_id, p.name + (f" ({p.identifier})" if p.identifier else "")

    async def _store_sales_category(self, actor_id: uuid.UUID):
        from app.accounts.models import AccountCategory, EntryKind

        cat = (await self.session.execute(select(AccountCategory).where(AccountCategory.kind == EntryKind.INCOME,
                                                                       func.lower(AccountCategory.name) == STORE_SALES_CATEGORY.lower()))).scalar_one_or_none()
        if cat is None:
            cat = AccountCategory(kind=EntryKind.INCOME, name=STORE_SALES_CATEGORY, is_system=True, is_active=True)
            self.session.add(cat)
            await self.session.flush()
        return cat

    async def issue(self, data: dict, *, actor_id: uuid.UUID) -> StockMovement:
        item = await self._item(data["item_id"])
        if not item.is_active:
            raise BusinessRuleViolationError(f"{item.name} is switched off. Switch it on first.")
        qty = _d(data.get("quantity"))
        party_type, party_id, party_name = await self._party(data.get("party_type"), data.get("party_id"), data.get("department"))
        mode = ChargeMode(data.get("charge_mode") or "FREE")
        reason = (data.get("reason") or "").strip() or None
        fields = {"party_type": party_type, "party_id": party_id, "party_name": party_name, "charge_mode": mode}
        if mode == ChargeMode.FREE:
            return await self._move(item, MovementKind.ISSUED, qty, actor_id=actor_id, reason=reason or "Issued free", **fields)

        if party_type == "DEPARTMENT":
            raise BusinessRuleViolationError("A department cannot be charged. Issue it free, or issue it to a person.")
        price = _d(data.get("unit_price") if data.get("unit_price") is not None else item.sale_price)
        if price <= 0:
            raise BusinessRuleViolationError(f"Enter the price per {item.unit} (no sale price is set for {item.name}).")
        amount = _d(price * qty)
        fields.update(unit_price=price, charge_amount=amount)
        # Check everything before anything is written.
        method = (data.get("payment_method") or "CASH").upper()
        reference = (data.get("payment_reference") or "").strip() or None
        if mode == ChargeMode.PAID_NOW:
            if method not in PAY_METHODS:
                raise BusinessRuleViolationError("Choose how it was paid: cash, bank transfer, cheque or online.")
            if method != "CASH" and not reference:
                raise BusinessRuleViolationError("Enter the slip, transaction or cheque number (needed for anything except cash).")
        m = await self._move(item, MovementKind.ISSUED, qty, actor_id=actor_id, reason=reason, **fields)

        if mode == ChargeMode.PAID_NOW:
            from app.accounts.models import PaymentMethod
            from app.accounts.service import AccountsService

            today = institute_today()
            cat = await self._store_sales_category(actor_id)
            income = await AccountsService(self.session).create_income(user_id=actor_id, data={
                "category_id": cat.id, "received_from": party_name[:150], "amount": amount, "income_date": today,
                "payment_method": PaymentMethod(method), "reference_no": reference,
                "description": f"{item.name} x {qty.normalize():f} {item.unit} ({movement_ref(m)})"})
            m.income_entry_id, m.payment_method, m.payment_reference = income.id, method, reference
            m.receipt_number = f"STORE-{today.year}-{await next_sequence_value(self.session, f'store_receipt:{today.year}'):06d}"
        else:   # ADD_TO_DUES: a fine, paid and tracked like every other fine (and it blocks what unpaid fines block)
            from app.fines.models import FinePayerType, FineSource
            from app.fines.service import FineService

            fine = await FineService(self.session).issue(
                payer_type=FinePayerType(party_type), payer_id=party_id, amount=float(amount),
                reason=f"{item.name} x {qty.normalize():f} {item.unit} from the store ({movement_ref(m)})"[:500],
                source=FineSource.INVENTORY, actor_id=actor_id, related_entity_type="stock_movement", related_entity_id=m.id)
            m.fine_id = fine.id
        await self.session.flush()
        return m

    # ------------------------------------------------------------------ back in: returns
    async def _returned(self, issue_id: uuid.UUID) -> Decimal:
        total = (await self.session.execute(select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(StockMovement.issue_id == issue_id))).scalar_one()
        return _d(total)

    async def _is_reversed(self, movement_id: uuid.UUID) -> bool:
        return (await self.session.execute(select(StockMovement.id).where(StockMovement.reverses_id == movement_id))).first() is not None

    async def _store_refunds_category(self):
        from app.accounts.models import AccountCategory, EntryKind

        cat = (await self.session.execute(select(AccountCategory).where(AccountCategory.kind == EntryKind.EXPENSE,
                                                                       func.lower(AccountCategory.name) == STORE_REFUNDS_CATEGORY.lower()))).scalar_one_or_none()
        if cat is None:
            cat = AccountCategory(kind=EntryKind.EXPENSE, name=STORE_REFUNDS_CATEGORY, is_system=True, is_active=True)
            self.session.add(cat)
            await self.session.flush()
        return cat

    async def return_items(self, issue_id: uuid.UUID, quantity, *, reason: str | None, actor_id: uuid.UUID, refund: bool = False,
                           refund_method: str | None = None, refund_reference: str | None = None) -> StockMovement:
        issue = await self.session.get(StockMovement, issue_id)
        if issue is None or issue.kind != MovementKind.ISSUED:
            raise BusinessRuleViolationError("Choose the issue the items are coming back from.")
        if await self._is_reversed(issue.id):
            raise BusinessRuleViolationError("That issue was reversed; nothing is out.")
        out = -_d(issue.quantity) - await self._returned(issue.id)
        qty = _d(quantity or out)
        if qty > out:
            raise BusinessRuleViolationError(f"Only {out.normalize():f} of this issue is still out.")
        item = await self._item(issue.item_id)
        note = (reason or "").strip()
        charged = bool(issue.charge_mode and issue.charge_mode != ChargeMode.FREE)
        if refund and not charged:
            raise BusinessRuleViolationError("Nothing was charged for this issue, so there is nothing to refund.")
        if charged and not refund:
            note = (note + " " if note else "") + "(was charged: no refund given)"
        value = _d(_d(issue.unit_price) * qty) if charged else Decimal(0)
        method = (refund_method or "CASH").upper()
        reference = (refund_reference or "").strip() or None
        fine = None
        if refund and issue.fine_id:
            from app.fines.models import Fine, FineStatus

            fine = await self.session.get(Fine, issue.fine_id)
        pay_back = bool(refund and (issue.charge_mode == ChargeMode.PAID_NOW or (fine is not None and fine.status == FineStatus.PAID)))
        if pay_back:
            if method not in PAY_METHODS:
                raise BusinessRuleViolationError("Choose how the money is paid back: cash, bank transfer, cheque or online.")
            if method != "CASH" and not reference:
                raise BusinessRuleViolationError("Enter the slip, transaction or cheque number (needed for anything except cash).")
        moved = await self._move(item, MovementKind.RETURNED, qty, unit_cost=_d(issue.unit_cost), actor_id=actor_id, reason=note or "Returned",
                                 party_type=issue.party_type, party_id=issue.party_id, party_name=issue.party_name, issue_id=issue.id)
        if not refund:
            return moved
        if pay_back:
            from app.accounts.models import Expense, ExpenseSource, ExpenseStatus, PaymentMethod
            from app.accounts.period import ensure_open

            today = institute_today()
            await ensure_open(self.session, today, "This refund")
            cat = await self._store_refunds_category()
            self.session.add(Expense(category_id=cat.id, title=f"Store refund: {item.name} x {qty.normalize():f}", description=f"Returned by {issue.party_name} ({movement_ref(issue)}).",
                                     amount=value, expense_date=today, payment_method=PaymentMethod(method), reference_no=reference, status=ExpenseStatus.APPROVED,
                                     source=ExpenseSource.MANUAL, recorded_by=actor_id, decided_by=actor_id, decided_at=datetime.now(timezone.utc)))
        elif fine is not None and fine.status == FineStatus.PENDING:        # sold on credit and not yet paid: the dues shrink (or vanish)
            left = _d(_d(fine.amount) - value)
            if left <= 0:
                from app.fines.service import FineService

                await FineService(self.session).waive(fine.id, actor_id=actor_id, note=f"Items returned ({movement_ref(issue)})")
            else:
                fine.amount = float(left)
        await self.session.flush()
        return moved

    # ------------------------------------------------------------------ corrections
    async def adjust(self, item_id: uuid.UUID, change, *, reason: str, actor_id: uuid.UUID) -> StockMovement:
        reason = (reason or "").strip()
        if len(reason) < 5:
            raise BusinessRuleViolationError("Say why the stock is being corrected (damaged, lost, count difference...).")
        change = _d(change)
        if change == 0:
            raise BusinessRuleViolationError("The change cannot be 0.")
        item = await self._item(item_id)
        return await self._move(item, MovementKind.ADJUSTED, abs(change), actor_id=actor_id, reason=reason, outgoing=change < 0,
                                unit_cost=None)

    async def reverse(self, movement_id: uuid.UUID, *, reason: str, actor_id: uuid.UUID) -> StockMovement:
        """Undo a movement entered by mistake: a new opposite row. Money follows: a sale's income entry is voided,
        an unpaid dues fine is waived (a fine already paid must be refunded first)."""
        reason = (reason or "").strip()
        if len(reason) < 5:
            raise BusinessRuleViolationError("Say why this is being reversed.")
        m = await self.session.get(StockMovement, movement_id)
        if m is None:
            raise ResourceNotFoundError("Stock movement not found.")
        if m.kind == MovementKind.REVERSED:
            raise BusinessRuleViolationError("A reversal cannot itself be reversed. Record the movement again instead.")
        if await self._is_reversed(m.id):
            raise BusinessRuleViolationError("This movement was already reversed.")
        if m.kind == MovementKind.ISSUED and await self._returned(m.id) != 0:
            raise BusinessRuleViolationError("Some of this issue was returned. Reverse the return first.")
        item = await self._item(m.item_id)
        if _d(m.quantity) > 0 and _d(m.quantity) > _d(item.quantity_on_hand):
            raise BusinessRuleViolationError(f"Only {_d(item.quantity_on_hand).normalize():f} {item.unit} of {item.name} in stock: "
                                             "the items this brought in have already gone out.")
        if m.fine_id is not None:
            from app.fines.models import Fine, FineStatus
            from app.fines.service import FineService

            fine = await self.session.get(Fine, m.fine_id)
            if fine is not None and fine.status == FineStatus.PAID:
                raise BusinessRuleViolationError("The charge for this issue was already paid. Refund it first, then reverse.")
            if fine is not None and fine.status == FineStatus.PENDING:
                await FineService(self.session).waive(fine.id, actor_id=actor_id, note=f"Store issue reversed: {reason}"[:500])
        if m.income_entry_id is not None:
            from app.accounts.service import AccountsService

            await AccountsService(self.session).void_income(m.income_entry_id, admin_id=actor_id, reason=f"Store issue reversed: {reason}")
        qty = abs(_d(m.quantity))
        return await self._move(item, MovementKind.REVERSED, qty, unit_cost=_d(m.unit_cost), actor_id=actor_id,
                                reason=f"Reverses {movement_ref(m)}: {reason}", outgoing=_d(m.quantity) > 0, reverses_id=m.id,
                                issue_id=m.issue_id if m.kind == MovementKind.RETURNED else None,
                                purchase_item_id=m.purchase_item_id, purchase_order_id=m.purchase_order_id,
                                party_type=m.party_type, party_id=m.party_id, party_name=m.party_name)

    # ------------------------------------------------------------------ reading
    async def items(self, *, q: str | None, category: str | None, low_only: bool, include_inactive: bool) -> list[dict]:
        stmt = select(StockItem).order_by(StockItem.category, StockItem.name)
        if not include_inactive:
            stmt = stmt.where(StockItem.is_active.is_(True))
        if category:
            stmt = stmt.where(StockItem.category == category)
        if q and q.strip():
            n = f"%{q.strip().lower()}%"
            stmt = stmt.where(func.lower(StockItem.name).like(n) | func.lower(StockItem.code).like(n))
        out = []
        for i in (await self.session.execute(stmt)).scalars():
            low = _f(i.reorder_level) > 0 and _f(i.quantity_on_hand) <= _f(i.reorder_level)
            if low_only and not low:
                continue
            out.append({"id": i.id, "code": i.code, "name": i.name, "category": i.category, "unit": i.unit, "sale_price": _f(i.sale_price) if i.sale_price is not None else None,
                        "reorder_level": _f(i.reorder_level), "is_returnable": i.is_returnable, "is_active": i.is_active,
                        "quantity_on_hand": _f(i.quantity_on_hand), "average_cost": _f(i.average_cost),
                        "stock_value": round(_f(i.quantity_on_hand) * _f(i.average_cost), 2), "low_stock": low})
        return out

    async def _out_rows(self, rows: list[StockMovement]) -> list[dict]:
        from app.auth.models import User

        items = {i.id: i for i in (await self.session.execute(select(StockItem).where(StockItem.id.in_({r.item_id for r in rows} or {uuid.uuid4()})))).scalars()}
        users = {u.id: u.username for u in (await self.session.execute(select(User).where(User.id.in_({r.recorded_by for r in rows if r.recorded_by} or {uuid.uuid4()})))).scalars()}
        reversed_ids = set((await self.session.execute(select(StockMovement.reverses_id).where(StockMovement.reverses_id.in_([r.id for r in rows] or [uuid.uuid4()])))).scalars())
        returned = {}
        issue_ids = [r.id for r in rows if r.kind == MovementKind.ISSUED]
        if issue_ids:
            returned = {iid: _f(q) for iid, q in (await self.session.execute(select(StockMovement.issue_id, func.sum(StockMovement.quantity))
                                                                            .where(StockMovement.issue_id.in_(issue_ids)).group_by(StockMovement.issue_id))).all()}
        out = []
        for r in rows:
            it = items.get(r.item_id)
            out.append({"id": r.id, "reference": movement_ref(r), "created_at": r.created_at, "kind": r.kind.value, "item_id": r.item_id,
                        "item_code": it.code if it else "?", "item_name": it.name if it else "?", "unit": it.unit if it else "",
                        "quantity": _f(r.quantity), "unit_cost": _f(r.unit_cost), "value": round(abs(_f(r.quantity)) * _f(r.unit_cost), 2),
                        "balance_after": _f(r.balance_after), "party_type": r.party_type, "party_id": r.party_id, "party_name": r.party_name,
                        "charge_mode": r.charge_mode.value if r.charge_mode else None, "unit_price": _f(r.unit_price) if r.unit_price is not None else None,
                        "charge_amount": _f(r.charge_amount) if r.charge_amount is not None else None, "payment_method": r.payment_method,
                        "payment_reference": r.payment_reference, "receipt_number": r.receipt_number, "fine_id": r.fine_id,
                        "purchase_order_id": r.purchase_order_id, "reason": r.reason, "recorded_by": users.get(r.recorded_by),
                        "reversed": r.id in reversed_ids, "returned": returned.get(r.id, 0.0),
                        "still_out": round(-_f(r.quantity) - returned.get(r.id, 0.0), 2) if r.kind == MovementKind.ISSUED and r.id not in reversed_ids else 0.0})
        return out

    async def movements(self, *, item_id: uuid.UUID | None, kind: MovementKind | None, party_type: str | None, party_id: uuid.UUID | None,
                        q: str | None, from_date: date | None, to_date: date | None, limit: int, offset: int) -> list[dict]:
        stmt = select(StockMovement).order_by(StockMovement.serial_no.desc())
        if item_id:
            stmt = stmt.where(StockMovement.item_id == item_id)
        if kind:
            stmt = stmt.where(StockMovement.kind == kind)
        if party_type:
            stmt = stmt.where(StockMovement.party_type == party_type.upper())
        if party_id:
            stmt = stmt.where(StockMovement.party_id == party_id)
        if q and q.strip():
            stmt = stmt.where(func.lower(StockMovement.party_name).like(f"%{q.strip().lower()}%"))
        if from_date:
            stmt = stmt.where(func.date(StockMovement.created_at) >= from_date)
        if to_date:
            stmt = stmt.where(func.date(StockMovement.created_at) <= to_date)
        rows = list((await self.session.execute(stmt.offset(max(0, offset)).limit(max(1, min(limit, 500))))).scalars())
        return await self._out_rows(rows)

    async def holdings(self) -> list[dict]:
        """Returnable items that are still with someone."""
        stmt = (select(StockMovement).join(StockItem, StockItem.id == StockMovement.item_id)
                .where(StockMovement.kind == MovementKind.ISSUED, StockItem.is_returnable.is_(True)).order_by(StockMovement.serial_no.desc()))
        rows = await self._out_rows(list((await self.session.execute(stmt.limit(2000))).scalars()))
        return [r for r in rows if r["still_out"] > 0.005]

    async def summary(self) -> dict:
        items = await self.items(q=None, category=None, low_only=False, include_inactive=False)
        today = institute_today()
        first = today.replace(day=1)
        month = (await self.session.execute(select(StockMovement).where(func.date(StockMovement.created_at) >= first))).scalars().all()
        reversed_ids = set((await self.session.execute(select(StockMovement.reverses_id).where(StockMovement.reverses_id.is_not(None)))).scalars())
        live = [m for m in month if m.id not in reversed_ids and m.kind != MovementKind.REVERSED]
        val = lambda ms: round(sum(abs(_f(m.quantity)) * _f(m.unit_cost) for m in ms), 2)   # noqa: E731
        issued = [m for m in live if m.kind == MovementKind.ISSUED]
        return {"items": len(items), "stock_value": round(sum(i["stock_value"] for i in items), 2), "low_stock": sum(1 for i in items if i["low_stock"]),
                "month": {"from": first, "received_value": val([m for m in live if m.kind == MovementKind.RECEIVED]),
                          "issued_free_cost": val([m for m in issued if m.charge_mode == ChargeMode.FREE]),
                          "sold_amount": round(sum(_f(m.charge_amount) for m in issued if m.charge_mode == ChargeMode.PAID_NOW), 2),
                          "charged_to_dues": round(sum(_f(m.charge_amount) for m in issued if m.charge_mode == ChargeMode.ADD_TO_DUES), 2),
                          "issues": len(issued)},
                "with_people": len(await self.holdings()), "purchases_waiting": len(await self.purchases_to_stock())}

    async def movement(self, movement_id: uuid.UUID) -> StockMovement:
        m = await self.session.get(StockMovement, movement_id)
        if m is None:
            raise ResourceNotFoundError("Stock movement not found.")
        return m