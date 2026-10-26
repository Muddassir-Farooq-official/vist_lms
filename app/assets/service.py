"""Fixed asset register: adding assets (bought, owned before the system, donated), moving them, monthly depreciation,
disposal with approval, physical checks, labels.

Rules kept here:
- An asset's book value = cost - depreciation used up before the system (opening) - posted depreciation entries.
  Cost and posted depreciation are never edited: a wrong cost can be corrected only while no depreciation has been
  posted for the asset; after that, reverse the latest run first.
- Depreciation is posted month by month in runs. Only the latest run can be reversed, and never inside closed books.
- Disposal is asked for by the office and approved by the administration (not by whoever asked, unless they are the
  only administrator). Approval posts the last part of the depreciation, keeps the book value on that day and records
  any money received as income in Accounts.
- Each change (place, holder, condition, status, repair, check, disposal) is an AssetEvent row and an audit log entry.
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.period import ensure_open
from app.assets import depreciation as dep
from app.assets.models import (Asset, AssetCategory, AssetCondition, AssetDisposal, AssetEvent, AssetLocation, AssetSource, AssetStatus,
                               AssetVerification, AssetVerificationLine, DepreciationEntry, DepreciationMethod, DepreciationRun, DisposalMethod)
from app.audit.service import AuditService
from app.core.clock import institute_today
from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError, DuplicateResourceError, ResourceNotFoundError, ValidationAppError
from app.core.sequences import next_sequence_value

money = dep.money
ASSET_SALES_CATEGORY = "Sale of fixed assets"
PAY_METHODS = {"CASH", "BANK_TRANSFER", "CHEQUE", "ONLINE"}
CODE_RE = re.compile(r"^[A-Z0-9]{2,10}$")

# Suggested categories. The rates are the ones commonly used in Pakistan (Income Tax Ordinance 2001, Third Schedule,
# reducing balance). They are only a starting point: the accountant / auditor confirms or changes them.
SUGGESTED_CATEGORIES = [
    ("LAND", "Land", "NONE", None, None, 0),
    ("BLDG", "Buildings", "REDUCING_BALANCE", None, 10, 0),
    ("FURN", "Furniture and fixtures", "REDUCING_BALANCE", None, 15, 0),
    ("IT", "Computers and IT equipment", "REDUCING_BALANCE", None, 30, 0),
    ("LAB", "Laboratory and teaching equipment", "REDUCING_BALANCE", None, 15, 0),
    ("OFF", "Office equipment (copiers, ACs, generators)", "REDUCING_BALANCE", None, 15, 0),
    ("VEH", "Vehicles", "REDUCING_BALANCE", None, 15, 0),
]


def _f(v) -> float | None:
    return None if v is None else float(v)


def _clean(v, n: int | None = None) -> str | None:
    s = " ".join(str(v or "").split())
    return (s[:n] if n else s) or None


def tag_prefix() -> str:
    return (getattr(settings, "asset_tag_prefix", None) or "VIST").strip().upper()


class AssetService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ================================================================== lookups
    async def _category(self, category_id) -> AssetCategory:
        c = await self.session.get(AssetCategory, category_id) if category_id else None
        if c is None:
            raise ResourceNotFoundError("Asset category not found.")
        return c

    async def _location(self, location_id) -> AssetLocation | None:
        if not location_id:
            return None
        loc = await self.session.get(AssetLocation, location_id)
        if loc is None:
            raise ResourceNotFoundError("Location not found.")
        return loc

    async def _employee(self, employee_id):
        from app.employees.models import Employee

        if not employee_id:
            return None
        e = await self.session.get(Employee, employee_id)
        if e is None:
            raise ResourceNotFoundError("Employee not found.")
        return e

    async def _asset(self, asset_id, *, lock: bool = False) -> Asset:
        stmt = select(Asset).where(Asset.id == asset_id)
        if lock:
            stmt = stmt.with_for_update()
        a = (await self.session.execute(stmt)).scalar_one_or_none()
        if a is None:
            raise ResourceNotFoundError("Asset not found.")
        return a

    async def by_tag(self, tag: str) -> Asset:
        t = (tag or "").strip()
        m = re.search(r"[#?&]tag=([^&#]+)", t)          # a scanned label QR is a link that ends in ...#tag=VIST-IT-0001
        if m:
            t = m.group(1)
        t = t.strip().upper()
        a = (await self.session.execute(select(Asset).where(func.upper(Asset.tag) == t))).scalar_one_or_none()
        if a is None:
            raise ResourceNotFoundError(f"No asset with tag {t}.")
        return a

    def terms(self, a: Asset, c: AssetCategory) -> dep.Terms:
        method = (a.method or c.method).value
        cost = money(a.cost)
        residual = money(a.residual_value) if a.residual_value is not None else money(cost * Decimal(str(c.residual_percent or 0)) / 100)
        life = a.useful_life_years if a.useful_life_years is not None else c.useful_life_years
        rate = a.rate_percent if a.rate_percent is not None else c.rate_percent
        return dep.Terms(method=method, cost=cost, residual=residual, life_years=Decimal(str(life)) if life is not None else None,
                         rate_percent=Decimal(str(rate)) if rate is not None else None)

    @staticmethod
    def _check_terms(t: dep.Terms) -> None:
        if t.method == "STRAIGHT_LINE" and not t.life_years:
            raise BusinessRuleViolationError("Straight line needs a useful life in years (on the asset or its category).")
        if t.method == "REDUCING_BALANCE" and not t.rate_percent:
            raise BusinessRuleViolationError("Reducing balance needs a yearly rate % (on the asset or its category).")
        if t.residual > t.cost:
            raise BusinessRuleViolationError("The residual value cannot be more than the cost.")

    async def _posted(self, asset_ids) -> dict:
        """Live (not reversed) depreciation per asset: {id: (total, last period_end)}."""
        ids = list(asset_ids)
        if not ids:
            return {}
        rows = (await self.session.execute(select(DepreciationEntry.asset_id, func.sum(DepreciationEntry.amount), func.max(DepreciationEntry.period_end))
                                           .where(DepreciationEntry.asset_id.in_(ids), DepreciationEntry.reversed_at.is_(None))
                                           .group_by(DepreciationEntry.asset_id))).all()
        return {r[0]: (money(r[1]), r[2]) for r in rows}

    async def book_value(self, a: Asset) -> Decimal:
        posted = (await self._posted([a.id])).get(a.id, (Decimal(0), None))[0]
        return money(a.cost) - money(a.opening_accumulated) - posted

    async def _event(self, a: Asset, kind: str, summary: str, *, actor_id, note: str | None = None, on: date | None = None,
                     cost=None, vendor_name: str | None = None) -> AssetEvent:
        e = AssetEvent(asset_id=a.id, kind=kind, happened_on=on or institute_today(), summary=summary[:300], note=_clean(note, 500),
                       cost=cost, vendor_name=_clean(vendor_name, 150), recorded_by=actor_id, recorded_at=datetime.now(timezone.utc))
        self.session.add(e)
        await self.audit.log(actor_user_id=actor_id, action=f"asset.{kind.lower()}", entity_type="asset", entity_id=a.id,
                             metadata={"tag": a.tag, "summary": summary[:300], "note": _clean(note, 500)})
        return e

    # ================================================================== categories and locations
    async def categories(self) -> list[dict]:
        counts = dict((await self.session.execute(select(Asset.category_id, func.count()).where(Asset.status != AssetStatus.DISPOSED)
                                                  .group_by(Asset.category_id))).all())
        out = []
        for c in (await self.session.execute(select(AssetCategory).order_by(AssetCategory.name))).scalars():
            out.append({"id": c.id, "code": c.code, "name": c.name, "method": c.method.value, "useful_life_years": _f(c.useful_life_years),
                        "rate_percent": _f(c.rate_percent), "residual_percent": _f(c.residual_percent), "is_active": c.is_active,
                        "assets": counts.get(c.id, 0)})
        return out

    def _category_values(self, data: dict, current: AssetCategory | None = None) -> dict:
        method = DepreciationMethod(data.get("method") or (current.method.value if current else "STRAIGHT_LINE"))
        life = data.get("useful_life_years", current.useful_life_years if current else None)
        rate = data.get("rate_percent", current.rate_percent if current else None)
        residual = data.get("residual_percent", current.residual_percent if current else 0) or 0
        if method == DepreciationMethod.STRAIGHT_LINE and not life:
            raise BusinessRuleViolationError("Straight line needs a useful life in years.")
        if method == DepreciationMethod.REDUCING_BALANCE and not rate:
            raise BusinessRuleViolationError("Reducing balance needs a yearly rate %.")
        if not 0 <= float(residual) < 100:
            raise BusinessRuleViolationError("The residual value is a % of cost from 0 to 99.")
        return {"method": method, "useful_life_years": life if method == DepreciationMethod.STRAIGHT_LINE else None,
                "rate_percent": rate if method == DepreciationMethod.REDUCING_BALANCE else None,
                "residual_percent": residual if method != DepreciationMethod.NONE else 0}

    async def create_category(self, data: dict, *, actor_id) -> AssetCategory:
        code = (data.get("code") or "").strip().upper()
        if not CODE_RE.match(code):
            raise BusinessRuleViolationError("The code is 2 to 10 letters or digits (it goes in the tag numbers, e.g. IT in VIST-IT-0001).")
        name = _clean(data.get("name"), 100)
        if not name:
            raise BusinessRuleViolationError("Give the category a name.")
        if (await self.session.execute(select(AssetCategory).where(or_(AssetCategory.code == code, func.lower(AssetCategory.name) == name.lower())))).scalar_one_or_none():
            raise DuplicateResourceError("A category with this code or name already exists.")
        c = AssetCategory(code=code, name=name, **self._category_values(data), is_active=True)
        self.session.add(c)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="asset.category_created", entity_type="asset_category", entity_id=c.id,
                             after={"code": code, "name": name, "method": c.method.value, "life": str(c.useful_life_years), "rate": str(c.rate_percent)})
        return c

    async def update_category(self, category_id, changes: dict, *, actor_id) -> AssetCategory:
        c = await self._category(category_id)
        before = {"name": c.name, "method": c.method.value, "life": str(c.useful_life_years), "rate": str(c.rate_percent), "residual": str(c.residual_percent)}
        if changes.get("name"):
            name = _clean(changes["name"], 100)
            if (await self.session.execute(select(AssetCategory).where(func.lower(AssetCategory.name) == name.lower(), AssetCategory.id != c.id))).scalar_one_or_none():
                raise DuplicateResourceError("Another category has this name.")
            c.name = name
        if any(k in changes for k in ("method", "useful_life_years", "rate_percent", "residual_percent")):
            for k, v in self._category_values(changes, c).items():
                setattr(c, k, v)
        if changes.get("is_active") is not None:
            c.is_active = bool(changes["is_active"])
        await self.audit.log(actor_user_id=actor_id, action="asset.category_updated", entity_type="asset_category", entity_id=c.id, before=before,
                             after={"name": c.name, "method": c.method.value, "life": str(c.useful_life_years), "rate": str(c.rate_percent), "residual": str(c.residual_percent)})
        return c

    async def add_suggested_categories(self, *, actor_id) -> list[str]:
        made = []
        for code, name, method, life, rate, residual in SUGGESTED_CATEGORIES:
            exists = (await self.session.execute(select(AssetCategory).where(or_(AssetCategory.code == code, func.lower(AssetCategory.name) == name.lower())))).scalar_one_or_none()
            if exists is None:
                await self.create_category({"code": code, "name": name, "method": method, "useful_life_years": life, "rate_percent": rate,
                                            "residual_percent": residual}, actor_id=actor_id)
                made.append(name)
        return made

    async def locations(self) -> list[dict]:
        counts = dict((await self.session.execute(select(Asset.location_id, func.count()).where(Asset.status != AssetStatus.DISPOSED)
                                                  .group_by(Asset.location_id))).all())
        return [{"id": loc.id, "name": loc.name, "building": loc.building, "department": loc.department, "is_active": loc.is_active,
                 "assets": counts.get(loc.id, 0)}
                for loc in (await self.session.execute(select(AssetLocation).order_by(AssetLocation.name))).scalars()]

    async def save_location(self, data: dict, *, actor_id, location_id=None) -> AssetLocation:
        loc = await self._location(location_id) if location_id else None
        name = _clean(data.get("name"), 120) if (loc is None or data.get("name")) else loc.name
        if not name:
            raise BusinessRuleViolationError("Give the place a name (e.g. Computer Lab 2, Room 14, Principal's office).")
        dup = (await self.session.execute(select(AssetLocation).where(func.lower(AssetLocation.name) == name.lower(),
                                                                       AssetLocation.id != (loc.id if loc else uuid.uuid4())))).scalar_one_or_none()
        if dup is not None:
            raise DuplicateResourceError(f"A place called '{dup.name}' already exists.")
        if loc is None:
            loc = AssetLocation(name=name, is_active=True)
            self.session.add(loc)
        loc.name = name
        for k, n in (("building", 120), ("department", 100)):
            if k in data:
                setattr(loc, k, _clean(data.get(k), n))
        if data.get("is_active") is not None:
            loc.is_active = bool(data["is_active"])
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="asset.location_saved", entity_type="asset_location", entity_id=loc.id,
                             after={"name": loc.name, "building": loc.building, "department": loc.department, "active": loc.is_active})
        return loc

    # ================================================================== adding assets
    async def _new_tag(self, c: AssetCategory) -> str:
        while True:
            tag = f"{tag_prefix()}-{c.code}-{await next_sequence_value(self.session, f'asset_tag:{c.code}'):04d}"
            if (await self.session.execute(select(Asset.id).where(func.upper(Asset.tag) == tag))).first() is None:
                return tag

    async def _check_tag(self, tag: str) -> str:
        t = (tag or "").strip().upper()
        if not re.match(r"^[A-Z0-9][A-Z0-9/_.-]{1,29}$", t):
            raise BusinessRuleViolationError(f"'{tag}' is not a usable tag: letters, digits, - / . _ only, up to 30.")
        if (await self.session.execute(select(Asset.id).where(func.upper(Asset.tag) == t))).first() is not None:
            raise DuplicateResourceError(f"Tag {t} is already used.")
        return t

    async def prepare(self, data: dict) -> tuple[dict, AssetCategory]:
        """Check one new asset and work out its numbers; nothing is written. Used by add, import and purchases."""
        c = await self._category(data.get("category_id"))
        if not c.is_active:
            raise BusinessRuleViolationError(f"The category {c.name} is switched off.")
        name = _clean(data.get("name"), 150)
        if not name:
            raise BusinessRuleViolationError("Give the asset a name (e.g. Dell OptiPlex 7090 desktop).")
        source = AssetSource(data.get("source") or "PURCHASE")
        today = institute_today()
        acquired = data.get("acquired_on")
        if acquired and acquired > today:
            raise BusinessRuleViolationError("The purchase date is in the future.")
        cost = money(data.get("cost"))
        if cost < 0:
            raise BusinessRuleViolationError("The cost cannot be negative.")
        estimate = bool(data.get("cost_is_estimate"))
        go_live = data.get("depreciation_from")
        if source == AssetSource.OPENING:
            # Owned before the system. Either the real cost and purchase date are known (the system works out what has
            # been used up so far) or only today's value is estimated (that value is depreciated from the go-live date).
            go_live = go_live or today.replace(day=1)
            if not estimate and not acquired:
                raise BusinessRuleViolationError("For an asset owned before the system, give its purchase date and cost, or tick "
                                                 "'value is an estimate' and enter what it is worth now.")
            if acquired and acquired >= go_live:
                raise BusinessRuleViolationError("An asset owned before the system was bought before the date depreciation starts here.")
        else:
            if not acquired:
                raise BusinessRuleViolationError("Give the date it was bought or received.")
            go_live = go_live or acquired
            if go_live < acquired:
                raise BusinessRuleViolationError("Depreciation cannot start before the asset was bought.")
        method = DepreciationMethod(data["method"]) if data.get("method") else None
        probe = Asset(cost=cost, method=method, useful_life_years=data.get("useful_life_years"), rate_percent=data.get("rate_percent"),
                      residual_value=data.get("residual_value"))
        t = self.terms(probe, c)
        self._check_terms(t)
        opening = Decimal("0.00")
        if source == AssetSource.OPENING and not estimate:
            opening = dep.used_before(t, acquired, go_live)
        values = {"name": name, "category_id": c.id, "description": _clean(data.get("description"), 500), "make_model": _clean(data.get("make_model"), 150),
                  "serial_number": _clean(data.get("serial_number"), 100), "location_id": data.get("location_id"), "department": _clean(data.get("department"), 100),
                  "custodian_employee_id": data.get("custodian_employee_id"), "condition": AssetCondition(data.get("condition") or "GOOD"),
                  "status": AssetStatus(data.get("status") or "IN_USE"), "warranty_until": data.get("warranty_until"), "source": source,
                  "acquired_on": acquired, "cost": cost, "cost_is_estimate": estimate, "vendor_id": data.get("vendor_id"),
                  "purchase_order_id": data.get("purchase_order_id"), "purchase_item_id": data.get("purchase_item_id"),
                  "invoice_no": _clean(data.get("invoice_no"), 60), "depreciation_from": go_live, "opening_accumulated": opening, "method": method,
                  "useful_life_years": data.get("useful_life_years"), "rate_percent": data.get("rate_percent"), "residual_value": data.get("residual_value"),
                  "notes": _clean(data.get("notes"))}
        if values["status"] == AssetStatus.DISPOSED:
            raise BusinessRuleViolationError("A new asset cannot be entered as disposed.")
        await self._location(values["location_id"])
        await self._employee(values["custodian_employee_id"])
        return values, c

    async def create(self, data: dict, *, actor_id, quantity: int = 1) -> list[Asset]:
        """Add one asset, or several identical ones (each gets its own tag)."""
        if not 1 <= quantity <= 500:
            raise BusinessRuleViolationError("Add between 1 and 500 at a time.")
        if data.get("tag") and quantity > 1:
            raise BusinessRuleViolationError("A tag you type can only be used for one asset; leave it empty to number several.")
        values, c = await self.prepare(data)
        made = []
        for _ in range(quantity):
            tag = await self._check_tag(data["tag"]) if data.get("tag") else await self._new_tag(c)
            a = Asset(tag=tag, created_by=actor_id, **values)
            self.session.add(a)
            await self.session.flush()
            src = {"PURCHASE": "Bought", "OPENING": "Owned before the system", "DONATION": "Donated", "OTHER": "Added"}[a.source.value]
            summary = f"{src}: cost {money(a.cost):,.2f}" + (" (estimated value)" if a.cost_is_estimate else "")
            if a.opening_accumulated:
                summary += f", depreciation already used up {money(a.opening_accumulated):,.2f}"
            await self._event(a, "ADDED", summary, actor_id=actor_id, on=a.acquired_on or a.depreciation_from)
            made.append(a)
        return made

    # ------------------------------------------------------------------ CSV import of assets owned before the system
    IMPORT_COLUMNS = ["tag", "name", "category", "location", "department", "custodian_employee_code", "make_model", "serial_number",
                      "acquired_on", "cost", "cost_is_estimate", "condition", "useful_life_years", "rate_percent", "residual_value", "notes"]

    async def import_csv(self, text: str, *, go_live: date | None, commit: bool, actor_id) -> dict:
        """Check every row first; only when every row is good (and commit is asked for) is anything added."""
        from app.employees.models import Employee

        rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
        if not rows:
            raise ValidationAppError("The file has no rows. The first line must be the column names.")
        if len(rows) > 5000:
            raise ValidationAppError("At most 5,000 rows per file.")
        cats = {}
        for c in (await self.session.execute(select(AssetCategory))).scalars():
            cats[c.code.upper()] = c
            cats[c.name.lower()] = c
        locs = {loc.name.lower(): loc for loc in (await self.session.execute(select(AssetLocation))).scalars()}
        emps = {e.employee_code.upper(): e for e in (await self.session.execute(select(Employee))).scalars()}
        go_live = go_live or institute_today().replace(day=1)
        checked, errors, seen_tags, new_places = [], [], set(), set()
        for n, raw in enumerate(rows, start=2):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            problems = []
            c = cats.get((r.get("category") or "").upper()) or cats.get((r.get("category") or "").lower())
            if c is None:
                problems.append(f"category '{r.get('category')}' not found (use its code or name)")
            place = r.get("location") or ""
            loc = locs.get(place.lower())
            if place and loc is None:
                new_places.add(place)
            emp = None
            if r.get("custodian_employee_code"):
                emp = emps.get(r["custodian_employee_code"].upper())
                if emp is None:
                    problems.append(f"employee {r['custodian_employee_code']} not found")

            def num(key):
                v = (r.get(key) or "").replace(",", "")
                if not v:
                    return None
                try:
                    return Decimal(v)
                except Exception:
                    problems.append(f"{key} '{r.get(key)}' is not a number")
                    return None

            def day(key):
                v = r.get(key)
                if not v:
                    return None
                for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                    try:
                        return datetime.strptime(v, fmt).date()
                    except ValueError:
                        pass
                problems.append(f"{key} '{v}' is not a date (YYYY-MM-DD or DD/MM/YYYY)")
                return None

            cost = num("cost")
            if cost is None and not r.get("cost"):
                problems.append("cost is missing")
            estimate = (r.get("cost_is_estimate") or "").lower() in ("1", "yes", "y", "true")
            data = {"name": r.get("name"), "category_id": c.id if c else None, "department": r.get("department"), "custodian_employee_id": emp.id if emp else None,
                    "make_model": r.get("make_model"), "serial_number": r.get("serial_number"), "acquired_on": day("acquired_on"), "cost": cost,
                    "cost_is_estimate": estimate, "condition": (r.get("condition") or "GOOD").upper(), "useful_life_years": num("useful_life_years"),
                    "rate_percent": num("rate_percent"), "residual_value": num("residual_value"), "notes": r.get("notes"), "source": "OPENING",
                    "depreciation_from": go_live, "tag": r.get("tag") or None}
            if data["condition"] not in AssetCondition.__members__:
                problems.append("condition must be GOOD, FAIR, POOR or UNUSABLE")
            tag = (data["tag"] or "").upper()
            if tag:
                if tag in seen_tags:
                    problems.append(f"tag {tag} appears twice in the file")
                seen_tags.add(tag)
                if (await self.session.execute(select(Asset.id).where(func.upper(Asset.tag) == tag))).first() is not None:
                    problems.append(f"tag {tag} is already used")
            values = None
            if not problems:
                try:
                    values, _c = await self.prepare(data)
                except (BusinessRuleViolationError, ResourceNotFoundError) as exc:
                    problems.append(str(exc.args[0] if exc.args else exc))
            if problems:
                errors.append({"row": n, "name": r.get("name"), "problems": problems})
            else:
                checked.append((data, values, place))
        result = {"rows": len(rows), "good": len(checked), "errors": errors[:200], "new_places": sorted(new_places), "imported": 0,
                  "total_cost": float(sum(v["cost"] for _d, v, _p in checked)),
                  "total_book_value": float(sum(v["cost"] - v["opening_accumulated"] for _d, v, _p in checked))}
        if errors or not commit:
            return result
        for place in sorted(new_places):
            locs[place.lower()] = await self.save_location({"name": place}, actor_id=actor_id)
        for data, _values, place in checked:
            data["location_id"] = locs[place.lower()].id if place else None
            await self.create(data, actor_id=actor_id)
        result["imported"] = len(checked)
        await self.audit.log(actor_user_id=actor_id, action="asset.imported", entity_type="asset", entity_id=None,
                             metadata={"rows": len(checked), "go_live": go_live.isoformat(), "total_cost": result["total_cost"]})
        return result

    # ------------------------------------------------------------------ from received purchase orders
    async def _registered(self, purchase_item_id) -> int:
        return (await self.session.execute(select(func.count()).select_from(Asset).where(Asset.purchase_item_id == purchase_item_id))).scalar_one()

    async def purchases_to_register(self) -> list[dict]:
        """Received purchase-order lines not yet put into the store or the asset register."""
        from app.inventory.service import InventoryService
        from app.purchasing.models import PurchaseItem, PurchaseOrder, PurchaseStatus

        inv = InventoryService(self.session)
        orders = (await self.session.execute(select(PurchaseOrder).where(PurchaseOrder.status.in_([PurchaseStatus.RECEIVED, PurchaseStatus.PAID]))
                                             .order_by(PurchaseOrder.received_at.desc().nullslast()).limit(200))).scalars().all()
        out = []
        for o in orders:
            lines = []
            for pi in (await self.session.execute(select(PurchaseItem).where(PurchaseItem.order_id == o.id).order_by(PurchaseItem.position))).scalars():
                got = money(pi.received_quantity if pi.received_quantity is not None else pi.quantity)
                left = got - await inv._stocked(pi.id)            # _stocked counts the store AND the assets registered from this line
                if left >= 1:
                    lines.append({"purchase_item_id": pi.id, "description": pi.description, "received": float(got), "left": int(left),
                                  "unit_cost": float(pi.actual_unit_price if pi.actual_unit_price is not None else pi.estimated_unit_price)})
            if lines:
                out.append({"order_id": o.id, "reference": f"PO-{o.created_at.year}-{o.serial_no:05d}", "title": o.title, "status": o.status.value,
                            "invoice_no": o.invoice_no, "vendor_id": o.vendor_id, "received_on": o.received_at.date() if o.received_at else None, "lines": lines})
        return out

    async def register_from_purchase(self, order_id, line: dict, *, actor_id) -> list[Asset]:
        from app.core.clock import local_date
        from app.inventory.service import InventoryService
        from app.purchasing.models import PurchaseItem, PurchaseOrder, PurchaseStatus

        order = await self.session.get(PurchaseOrder, order_id)
        if order is None:
            raise ResourceNotFoundError("Purchase order not found.")
        if order.status not in (PurchaseStatus.RECEIVED, PurchaseStatus.PAID):
            raise BusinessRuleViolationError("Only goods that have been received (or paid for) can be added to the register.")
        pi = (await self.session.execute(select(PurchaseItem).where(PurchaseItem.id == line["purchase_item_id"]).with_for_update())).scalar_one_or_none()
        if pi is None or pi.order_id != order.id:
            raise BusinessRuleViolationError("That line does not belong to this purchase order.")
        got = money(pi.received_quantity if pi.received_quantity is not None else pi.quantity)
        left = int(got - await InventoryService(self.session)._stocked(pi.id))
        qty = int(line.get("quantity") or left)
        if qty < 1 or qty > left:
            raise BusinessRuleViolationError(f"'{pi.description}': {left} left to add.")
        received_on = local_date(order.received_at) if order.received_at else institute_today()
        data = {**{k: v for k, v in line.items() if k not in ("purchase_item_id", "quantity")},
                "name": line.get("name") or pi.description, "source": "PURCHASE", "acquired_on": received_on,
                "cost": pi.actual_unit_price if pi.actual_unit_price is not None else pi.estimated_unit_price, "vendor_id": order.vendor_id,
                "purchase_order_id": order.id, "purchase_item_id": pi.id, "invoice_no": order.invoice_no,
                "description": line.get("description") or f"From PO-{order.created_at.year}-{order.serial_no:05d}"}
        return await self.create(data, actor_id=actor_id, quantity=qty)

    # ================================================================== the register
    async def listing(self, *, q: str | None = None, category_id=None, location_id=None, status: str | None = None, custodian_id=None,
                      include_disposed: bool = False, limit: int = 500, offset: int = 0) -> dict:
        from app.employees.models import Employee

        stmt = select(Asset)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(Asset.tag.ilike(like), Asset.name.ilike(like), Asset.serial_number.ilike(like), Asset.make_model.ilike(like)))
        if category_id:
            stmt = stmt.where(Asset.category_id == category_id)
        if location_id:
            stmt = stmt.where(Asset.location_id == location_id)
        if custodian_id:
            stmt = stmt.where(Asset.custodian_employee_id == custodian_id)
        if status:
            stmt = stmt.where(Asset.status == AssetStatus(status))
        elif not include_disposed:
            stmt = stmt.where(Asset.status != AssetStatus.DISPOSED)
        total = (await self.session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        assets = (await self.session.execute(stmt.order_by(Asset.tag).limit(limit).offset(offset))).scalars().all()
        cats = {c.id: c for c in (await self.session.execute(select(AssetCategory))).scalars()}
        locs = {loc.id: loc.name for loc in (await self.session.execute(select(AssetLocation))).scalars()}
        emp_ids = {a.custodian_employee_id for a in assets if a.custodian_employee_id}
        emps = {e.id: e for e in (await self.session.execute(select(Employee).where(Employee.id.in_(emp_ids)))).scalars()} if emp_ids else {}
        posted = await self._posted(a.id for a in assets)
        items = []
        for a in assets:
            p, last = posted.get(a.id, (Decimal(0), None))
            e = emps.get(a.custodian_employee_id)
            items.append({"id": a.id, "tag": a.tag, "name": a.name, "category_id": a.category_id, "category": cats[a.category_id].name,
                          "location_id": a.location_id, "location": locs.get(a.location_id), "department": a.department,
                          "custodian_employee_id": a.custodian_employee_id, "custodian": f"{e.full_name} ({e.employee_code})" if e else None,
                          "status": a.status.value, "condition": a.condition.value, "source": a.source.value, "acquired_on": a.acquired_on,
                          "cost": float(a.cost), "cost_is_estimate": a.cost_is_estimate, "accumulated": float(money(a.opening_accumulated) + p),
                          "book_value": float(money(a.cost) - money(a.opening_accumulated) - p), "depreciated_to": last,
                          "serial_number": a.serial_number, "make_model": a.make_model})
        return {"total": total, "items": items}

    async def summary(self) -> dict:
        live = select(Asset).where(Asset.status != AssetStatus.DISPOSED).subquery()
        count, cost, opening = (await self.session.execute(select(func.count(), func.coalesce(func.sum(live.c.cost), 0), func.coalesce(func.sum(live.c.opening_accumulated), 0))
                                                           .select_from(live))).one()
        posted = (await self.session.execute(select(func.coalesce(func.sum(DepreciationEntry.amount), 0)).join(Asset, Asset.id == DepreciationEntry.asset_id)
                                             .where(DepreciationEntry.reversed_at.is_(None), Asset.status != AssetStatus.DISPOSED))).scalar_one()
        by_status = dict((await self.session.execute(select(Asset.status, func.count()).group_by(Asset.status))).all())
        last_run = (await self.session.execute(select(DepreciationRun).where(DepreciationRun.reversed_at.is_(None)).order_by(DepreciationRun.period_end.desc()).limit(1))).scalar_one_or_none()
        pending = (await self.session.execute(select(func.count()).select_from(AssetDisposal).where(AssetDisposal.status == "REQUESTED"))).scalar_one()
        open_checks = (await self.session.execute(select(func.count()).select_from(AssetVerification).where(AssetVerification.status == "OPEN"))).scalar_one()
        today = institute_today()
        warranty = (await self.session.execute(select(func.count()).select_from(Asset).where(Asset.status != AssetStatus.DISPOSED, Asset.warranty_until >= today,
                                                                                              Asset.warranty_until <= today + timedelta(days=60)))).scalar_one()
        return {"assets": count, "cost": float(cost), "accumulated": float(money(opening) + money(posted)), "book_value": float(money(cost) - money(opening) - money(posted)),
                "missing": by_status.get(AssetStatus.MISSING, 0), "under_repair": by_status.get(AssetStatus.UNDER_REPAIR, 0),
                "disposed": by_status.get(AssetStatus.DISPOSED, 0), "depreciated_to": last_run.period_end if last_run else None,
                "next_period_end": self.next_period_end(last_run.period_end if last_run else None), "disposals_waiting": pending,
                "open_checks": open_checks, "warranty_ending": warranty}

    async def detail(self, asset_id) -> dict:
        from app.accounts.models import Vendor
        from app.accounts.service import AccountsService
        from app.employees.models import Employee

        a = await self._asset(asset_id)
        c = await self._category(a.category_id)
        t = self.terms(a, c)
        loc = await self._location(a.location_id)
        emp = await self.session.get(Employee, a.custodian_employee_id) if a.custodian_employee_id else None
        vendor = await self.session.get(Vendor, a.vendor_id) if a.vendor_id else None
        entries = (await self.session.execute(select(DepreciationEntry).where(DepreciationEntry.asset_id == a.id).order_by(DepreciationEntry.period_end))).scalars().all()
        events = (await self.session.execute(select(AssetEvent).where(AssetEvent.asset_id == a.id).order_by(AssetEvent.recorded_at))).scalars().all()
        disposals = (await self.session.execute(select(AssetDisposal).where(AssetDisposal.asset_id == a.id).order_by(AssetDisposal.created_at))).scalars().all()
        names = await AccountsService(self.session)._names({e.recorded_by for e in events} | {d.requested_by for d in disposals} | {d.decided_by for d in disposals})
        posted = sum((money(e.amount) for e in entries if e.reversed_at is None), Decimal(0))
        bv = money(a.cost) - money(a.opening_accumulated) - posted
        last = max((e.period_end for e in entries if e.reversed_at is None), default=None)
        nxt = (last + timedelta(days=1)) if last else a.depreciation_from
        return {
            "id": a.id, "tag": a.tag, "name": a.name, "description": a.description, "make_model": a.make_model, "serial_number": a.serial_number,
            "category_id": c.id, "category": c.name, "location_id": a.location_id, "location": loc.name if loc else None, "department": a.department,
            "custodian_employee_id": a.custodian_employee_id, "custodian": f"{emp.full_name} ({emp.employee_code})" if emp else None,
            "status": a.status.value, "condition": a.condition.value, "warranty_until": a.warranty_until, "source": a.source.value,
            "acquired_on": a.acquired_on, "cost": float(a.cost), "cost_is_estimate": a.cost_is_estimate, "vendor": vendor.name if vendor else None,
            "invoice_no": a.invoice_no, "purchase_order_id": a.purchase_order_id, "notes": a.notes, "disposed_on": a.disposed_on,
            "depreciation": {"method": t.method, "describe": t.describe(), "residual_value": float(t.residual), "starts": a.depreciation_from,
                             "opening_accumulated": float(a.opening_accumulated), "posted": float(posted), "book_value": float(bv), "depreciated_to": last,
                             "own_terms": any(v is not None for v in (a.method, a.useful_life_years, a.rate_percent, a.residual_value)),
                             "method_override": a.method.value if a.method else None, "useful_life_years": _f(a.useful_life_years),
                             "rate_percent": _f(a.rate_percent), "residual_override": _f(a.residual_value),
                             "projection": [] if a.status == AssetStatus.DISPOSED else dep.projection(t, bv, nxt, years=10)},
            "entries": [{"from": e.period_start, "to": e.period_end, "amount": float(e.amount), "book_value_after": float(e.book_value_after),
                         "reversed": e.reversed_at is not None, "disposal": e.run_id is None} for e in entries],
            "events": [{"kind": e.kind, "on": e.happened_on, "summary": e.summary, "note": e.note, "cost": _f(e.cost), "vendor_name": e.vendor_name,
                        "by": names.get(e.recorded_by), "at": e.recorded_at} for e in events],
            "disposals": [self._disposal_dict(d, a, names) for d in disposals],
            "label_url": f"/api/v1/assets/labels.pdf?ids={a.id}",
        }

    async def people(self, q: str) -> list[dict]:
        """Staff an asset can be handed to (name, code and department only: no pay or personal details)."""
        from app.employees.models import Employee, EmployeeStatus

        like = f"%{(q or '').strip()}%"
        rows = (await self.session.execute(select(Employee).where(Employee.status == EmployeeStatus.ACTIVE, or_(
            Employee.first_name.ilike(like), Employee.last_name.ilike(like), Employee.employee_code.ilike(like),
            (Employee.first_name + " " + Employee.last_name).ilike(like))).order_by(Employee.first_name).limit(15))).scalars().all()
        return [{"id": e.id, "name": e.full_name, "code": e.employee_code, "department": e.department} for e in rows]

    # ================================================================== changes
    async def update(self, asset_id, changes: dict, *, actor_id) -> Asset:
        """Details, place, holder, condition and status. Each change of place / holder / condition / status is a history line."""
        from app.employees.models import Employee

        a = await self._asset(asset_id, lock=True)
        if a.status == AssetStatus.DISPOSED:
            raise BusinessRuleViolationError("This asset has been disposed of; it can no longer be changed.")
        note = changes.get("note")
        on = changes.get("on") or institute_today()
        if on > institute_today():
            raise BusinessRuleViolationError("The date of a change cannot be in the future.")
        plain = {"name": 150, "description": 500, "make_model": 150, "serial_number": 100, "department": 100, "notes": None}
        before = {}
        for k, n in plain.items():
            if k in changes and changes[k] is not None:
                v = _clean(changes[k], n)
                if k == "name" and not v:
                    raise BusinessRuleViolationError("The name cannot be empty.")
                if v != getattr(a, k):
                    before[k] = getattr(a, k)
                    setattr(a, k, v)
        if "warranty_until" in changes and changes["warranty_until"] != a.warranty_until:
            before["warranty_until"] = str(a.warranty_until)
            a.warranty_until = changes["warranty_until"]
        if before:
            await self.audit.log(actor_user_id=actor_id, action="asset.details_updated", entity_type="asset", entity_id=a.id, before=before,
                                 after={k: str(getattr(a, k)) for k in before})
        if "location_id" in changes and changes["location_id"] != a.location_id:
            old, new = await self._location(a.location_id), await self._location(changes["location_id"])
            a.location_id = new.id if new else None
            await self._event(a, "MOVED", f"{old.name if old else 'No place'} -> {new.name if new else 'No place'}", actor_id=actor_id, note=note, on=on)
        if "custodian_employee_id" in changes and changes["custodian_employee_id"] != a.custodian_employee_id:
            old = await self.session.get(Employee, a.custodian_employee_id) if a.custodian_employee_id else None
            new = await self._employee(changes["custodian_employee_id"])
            a.custodian_employee_id = new.id if new else None
            await self._event(a, "CUSTODIAN", f"{old.full_name if old else 'Nobody'} -> {new.full_name if new else 'Nobody'}", actor_id=actor_id, note=note, on=on)
        if changes.get("condition") and AssetCondition(changes["condition"]) != a.condition:
            old, a.condition = a.condition, AssetCondition(changes["condition"])
            await self._event(a, "CONDITION", f"{old.value.title()} -> {a.condition.value.title()}", actor_id=actor_id, note=note, on=on)
        if changes.get("status") and AssetStatus(changes["status"]) != a.status:
            new = AssetStatus(changes["status"])
            if new == AssetStatus.DISPOSED:
                raise BusinessRuleViolationError("Use Dispose to take an asset off the books (it needs approval).")
            old, a.status = a.status, new
            await self._event(a, "STATUS", f"{old.value.replace('_', ' ').title()} -> {new.value.replace('_', ' ').title()}", actor_id=actor_id, note=note, on=on)
        return a

    async def correct(self, asset_id, changes: dict, reason: str, *, actor_id) -> Asset:
        """Correct the cost, dates or depreciation terms. Only while no depreciation has been posted for the asset."""
        a = await self._asset(asset_id, lock=True)
        if len((reason or "").strip()) < 10:
            raise BusinessRuleViolationError("Say why the figures are being corrected (at least 10 letters).")
        if a.status == AssetStatus.DISPOSED:
            raise BusinessRuleViolationError("This asset has been disposed of.")
        if await self._posted([a.id]):
            raise BusinessRuleViolationError("Depreciation has already been posted for this asset, so its cost and terms are fixed. "
                                             "Reverse the depreciation runs back to its start first, or dispose of it and add it again.")
        keys = ("category_id", "acquired_on", "cost", "cost_is_estimate", "depreciation_from", "method", "useful_life_years", "rate_percent", "residual_value", "source")
        before = {k: str(getattr(a, k)) for k in keys}
        data = {"name": a.name, "category_id": a.category_id, "source": a.source.value, "acquired_on": a.acquired_on, "cost": a.cost,
                "cost_is_estimate": a.cost_is_estimate, "depreciation_from": a.depreciation_from, "method": a.method.value if a.method else None,
                "useful_life_years": a.useful_life_years, "rate_percent": a.rate_percent, "residual_value": a.residual_value,
                "location_id": a.location_id, "custodian_employee_id": a.custodian_employee_id, "condition": a.condition.value, "status": a.status.value}
        data.update({k: v for k, v in changes.items() if k in keys})
        values, _c = await self.prepare(data)
        for k in keys + ("opening_accumulated",):
            setattr(a, k, values[k])
        after = {k: str(getattr(a, k)) for k in keys}
        diff = ", ".join(f"{k} {before[k]} -> {after[k]}" for k in keys if before[k] != after[k])
        if not diff:
            raise BusinessRuleViolationError("Nothing was changed.")
        await self._event(a, "CORRECTED", ("Corrected: " + diff)[:300], actor_id=actor_id, note=reason)
        return a

    async def add_maintenance(self, asset_id, data: dict, *, actor_id) -> AssetEvent:
        a = await self._asset(asset_id, lock=True)
        if a.status == AssetStatus.DISPOSED:
            raise BusinessRuleViolationError("This asset has been disposed of.")
        what = _clean(data.get("summary"), 250)
        if not what:
            raise BusinessRuleViolationError("Say what was done (e.g. 'Replaced power supply').")
        on = data.get("on") or institute_today()
        if on > institute_today():
            raise BusinessRuleViolationError("The date is in the future.")
        e = await self._event(a, "REPAIR", what, actor_id=actor_id, note=data.get("note"), on=on, cost=money(data["cost"]) if data.get("cost") else None,
                              vendor_name=data.get("vendor_name"))
        if data.get("status") and AssetStatus(data["status"]) != a.status:
            await self.update(a.id, {"status": data["status"], "note": what}, actor_id=actor_id)
        return e

    # ================================================================== depreciation runs
    @staticmethod
    def next_period_end(last: date | None) -> date:
        if last is None:
            prev = institute_today().replace(day=1) - timedelta(days=1)
            return prev
        return dep.month_end(last + timedelta(days=1))

    async def _plan(self, period_end: date, *, lock: bool = False) -> list[dict]:
        """What a run up to period_end would post, per asset."""
        stmt = select(Asset).where(Asset.status != AssetStatus.DISPOSED, Asset.depreciation_from <= period_end).order_by(Asset.tag)
        if lock:
            stmt = stmt.with_for_update()
        assets = (await self.session.execute(stmt)).scalars().all()
        cats = {c.id: c for c in (await self.session.execute(select(AssetCategory))).scalars()}
        posted = await self._posted(a.id for a in assets)
        plan = []
        for a in assets:
            t = self.terms(a, cats[a.category_id])
            total, last = posted.get(a.id, (Decimal(0), None))
            start = (last + timedelta(days=1)) if last else a.depreciation_from
            if start > period_end:
                continue
            bv = money(a.cost) - money(a.opening_accumulated) - total
            amount = dep.charge(t, bv, start, period_end)
            if amount <= 0:
                continue
            plan.append({"asset": a, "tag": a.tag, "name": a.name, "category": cats[a.category_id].name, "from": start, "to": period_end,
                         "book_value_before": float(bv), "amount": float(amount), "book_value_after": float(bv - amount), "method": t.describe()})
        return plan

    def _check_period_end(self, period_end: date) -> None:
        if period_end != dep.month_end(period_end):
            raise BusinessRuleViolationError("Depreciation is posted up to the last day of a month.")
        if period_end > dep.month_end(institute_today()):
            raise BusinessRuleViolationError("You cannot post depreciation for a month that has not started.")

    async def preview(self, period_end: date) -> dict:
        self._check_period_end(period_end)
        plan = await self._plan(period_end)
        last = await self._last_run()
        return {"period_end": period_end, "after_run": last.period_end if last else None, "total": round(sum(p["amount"] for p in plan), 2),
                "lines": [{k: v for k, v in p.items() if k != "asset"} for p in plan]}

    async def _last_run(self) -> DepreciationRun | None:
        return (await self.session.execute(select(DepreciationRun).where(DepreciationRun.reversed_at.is_(None))
                                           .order_by(DepreciationRun.period_end.desc()).limit(1))).scalar_one_or_none()

    async def post_run(self, period_end: date, *, actor_id) -> DepreciationRun:
        self._check_period_end(period_end)
        await ensure_open(self.session, period_end, "This depreciation")
        last = await self._last_run()
        if last is not None and period_end <= last.period_end:
            raise BusinessRuleViolationError(f"Depreciation is already posted up to {last.period_end:%d %b %Y}. Post a later month, or reverse that run first.")
        plan = await self._plan(period_end, lock=True)
        run = DepreciationRun(period_end=period_end, total=Decimal(0), assets_count=0, posted_by=actor_id)
        self.session.add(run)
        await self.session.flush()
        now = datetime.now(timezone.utc)
        total = Decimal(0)
        for p in plan:
            amt = money(p["amount"])
            self.session.add(DepreciationEntry(asset_id=p["asset"].id, run_id=run.id, period_start=p["from"], period_end=p["to"], amount=amt,
                                               book_value_after=money(p["book_value_after"]), created_at=now))
            total += amt
        run.total, run.assets_count = total, len(plan)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="asset.depreciation_posted", entity_type="depreciation_run", entity_id=run.id,
                             metadata={"period_end": period_end.isoformat(), "total": str(total), "assets": len(plan)})
        return run

    async def reverse_run(self, run_id, reason: str, *, actor_id) -> DepreciationRun:
        run = (await self.session.execute(select(DepreciationRun).where(DepreciationRun.id == run_id).with_for_update())).scalar_one_or_none()
        if run is None:
            raise ResourceNotFoundError("Depreciation run not found.")
        if run.reversed_at is not None:
            raise BusinessRuleViolationError("This run is already reversed.")
        if len((reason or "").strip()) < 10:
            raise BusinessRuleViolationError("Say why the run is being reversed (at least 10 letters).")
        last = await self._last_run()
        if last is None or last.id != run.id:
            raise BusinessRuleViolationError(f"Only the latest run can be reversed (that is {last.period_end:%B %Y}).")
        await ensure_open(self.session, run.period_end, "This depreciation")
        entries = (await self.session.execute(select(DepreciationEntry).where(DepreciationEntry.run_id == run.id))).scalars().all()
        later = (await self.session.execute(select(Asset.tag).join(DepreciationEntry, DepreciationEntry.asset_id == Asset.id)
                                            .where(DepreciationEntry.asset_id.in_([e.asset_id for e in entries] or [uuid.uuid4()]),
                                                   DepreciationEntry.reversed_at.is_(None), DepreciationEntry.period_start > run.period_end))).scalars().all()
        if later:
            raise BusinessRuleViolationError("These assets were disposed of after this run, so it cannot be undone: " + ", ".join(sorted(set(later))[:10]) + ".")
        now = datetime.now(timezone.utc)
        for e in entries:
            e.reversed_at = now
        run.reversed_at, run.reversed_by, run.reverse_reason = now, actor_id, reason.strip()[:300]
        await self.audit.log(actor_user_id=actor_id, action="asset.depreciation_reversed", entity_type="depreciation_run", entity_id=run.id,
                             metadata={"period_end": run.period_end.isoformat(), "total": str(run.total), "reason": reason.strip()})
        return run

    async def runs(self) -> list[dict]:
        from app.accounts.service import AccountsService

        rs = (await self.session.execute(select(DepreciationRun).order_by(DepreciationRun.period_end.desc(), DepreciationRun.created_at.desc()).limit(120))).scalars().all()
        names = await AccountsService(self.session)._names({r.posted_by for r in rs} | {r.reversed_by for r in rs})
        last = next((r for r in rs if r.reversed_at is None), None)
        return [{"id": r.id, "period_end": r.period_end, "total": float(r.total), "assets": r.assets_count, "posted_by": names.get(r.posted_by),
                 "posted_at": r.created_at, "reversed": r.reversed_at is not None, "reversed_by": names.get(r.reversed_by), "reverse_reason": r.reverse_reason,
                 "can_reverse": last is not None and r.id == last.id} for r in rs]

    # ================================================================== disposal
    def _disposal_dict(self, d: AssetDisposal, a: Asset | None, names: dict) -> dict:
        gain = None if d.book_value is None else float(money(d.proceeds) - money(d.book_value))
        return {"id": d.id, "asset_id": d.asset_id, "tag": a.tag if a else None, "name": a.name if a else None, "method": d.method.value,
                "disposed_on": d.disposed_on, "proceeds": float(d.proceeds), "payment_method": d.payment_method, "payment_reference": d.payment_reference,
                "buyer": d.buyer, "reason": d.reason, "status": d.status, "requested_by": names.get(d.requested_by), "requested_at": d.created_at,
                "decided_by": names.get(d.decided_by), "decided_at": d.decided_at, "decision_note": d.decision_note,
                "book_value": _f(d.book_value), "gain_or_loss": gain}

    async def request_disposal(self, asset_id, data: dict, *, actor_id) -> AssetDisposal:
        a = await self._asset(asset_id, lock=True)
        if a.status == AssetStatus.DISPOSED:
            raise BusinessRuleViolationError("This asset has already been disposed of.")
        if (await self.session.execute(select(AssetDisposal.id).where(AssetDisposal.asset_id == a.id, AssetDisposal.status == "REQUESTED"))).first():
            raise BusinessRuleViolationError("A disposal of this asset is already waiting for approval.")
        method = DisposalMethod(data["method"])
        on = data.get("disposed_on") or institute_today()
        if on > institute_today():
            raise BusinessRuleViolationError("The disposal date cannot be in the future.")
        if on < a.depreciation_from and (a.acquired_on is None or on < a.acquired_on):
            raise BusinessRuleViolationError("The disposal date is before the asset was added.")
        proceeds = money(data.get("proceeds"))
        if proceeds < 0:
            raise BusinessRuleViolationError("The amount received cannot be negative.")
        if proceeds > 0 and method != DisposalMethod.SOLD:
            raise BusinessRuleViolationError("Only a sale brings in money. Choose 'Sold', or leave the amount at 0.")
        pm = (data.get("payment_method") or "CASH").upper() if proceeds > 0 else None
        ref = _clean(data.get("payment_reference"), 80)
        if pm and pm not in PAY_METHODS:
            raise BusinessRuleViolationError("Choose how the money was received: cash, bank transfer, cheque or online.")
        if pm and pm != "CASH" and not ref:
            raise BusinessRuleViolationError("Enter the slip, transaction or cheque number (needed for anything except cash).")
        reason = _clean(data.get("reason"), 300)
        if not reason or len(reason) < 5:
            raise BusinessRuleViolationError("Say why it is being disposed of.")
        d = AssetDisposal(asset_id=a.id, method=method, disposed_on=on, proceeds=proceeds, payment_method=pm, payment_reference=ref,
                          buyer=_clean(data.get("buyer"), 150), reason=reason, status="REQUESTED", requested_by=actor_id)
        self.session.add(d)
        await self.session.flush()
        await self.audit.log(actor_user_id=actor_id, action="asset.disposal_requested", entity_type="asset_disposal", entity_id=d.id,
                             metadata={"tag": a.tag, "method": method.value, "on": on.isoformat(), "proceeds": str(proceeds), "reason": reason})
        return d

    async def decide_disposal(self, disposal_id, approve: bool, note: str | None, *, actor_id) -> AssetDisposal:
        from app.auth.permissions import Permissions
        from app.core.controls import four_eyes

        d = (await self.session.execute(select(AssetDisposal).where(AssetDisposal.id == disposal_id).with_for_update())).scalar_one_or_none()
        if d is None:
            raise ResourceNotFoundError("Disposal request not found.")
        if d.status != "REQUESTED":
            raise BusinessRuleViolationError("This request has already been decided.")
        sole = await four_eyes(self.session, actor_id, [d.requested_by], Permissions.ASSET_APPROVE,
                               "You asked for this disposal; another administrator must approve or reject it.")
        a = await self._asset(d.asset_id, lock=True)
        d.decided_by, d.decided_at, d.decision_note = actor_id, datetime.now(timezone.utc), _clean(note, 300)
        if not approve:
            if not d.decision_note:
                raise BusinessRuleViolationError("Say why the disposal is rejected.")
            d.status = "REJECTED"
            await self.audit.log(actor_user_id=actor_id, action="asset.disposal_rejected", entity_type="asset_disposal", entity_id=d.id,
                                 metadata={"tag": a.tag, "note": d.decision_note})
            return d
        if a.status == AssetStatus.DISPOSED:
            raise BusinessRuleViolationError("This asset has already been disposed of.")
        await ensure_open(self.session, d.disposed_on, "This disposal")
        c = await self._category(a.category_id)
        t = self.terms(a, c)
        total, last = (await self._posted([a.id])).get(a.id, (Decimal(0), None))
        if last is not None and last > d.disposed_on:
            raise BusinessRuleViolationError(f"Depreciation is already posted for this asset up to {last:%d %b %Y}, after the disposal date. "
                                             "Change the disposal date (ask again), or reverse the later runs first.")
        bv = money(a.cost) - money(a.opening_accumulated) - total
        start = (last + timedelta(days=1)) if last else a.depreciation_from
        final = dep.charge(t, bv, start, d.disposed_on) if d.disposed_on >= start else Decimal(0)
        if final > 0:
            self.session.add(DepreciationEntry(asset_id=a.id, run_id=None, period_start=start, period_end=d.disposed_on, amount=final,
                                               book_value_after=bv - final, created_at=datetime.now(timezone.utc)))
            bv -= final
        d.book_value, d.status = bv, "APPROVED"
        if d.proceeds and money(d.proceeds) > 0:
            from app.accounts.models import AccountCategory, EntryKind, PaymentMethod
            from app.accounts.service import AccountsService

            cat = (await self.session.execute(select(AccountCategory).where(AccountCategory.kind == EntryKind.INCOME,
                                                                            func.lower(AccountCategory.name) == ASSET_SALES_CATEGORY.lower()))).scalar_one_or_none()
            if cat is None:
                cat = AccountCategory(kind=EntryKind.INCOME, name=ASSET_SALES_CATEGORY, is_system=True, is_active=True)
                self.session.add(cat)
                await self.session.flush()
            income = await AccountsService(self.session).create_income(user_id=actor_id, data={
                "category_id": cat.id, "received_from": (d.buyer or "Buyer of a fixed asset")[:150], "amount": money(d.proceeds), "income_date": d.disposed_on,
                "payment_method": PaymentMethod(d.payment_method or "CASH"), "reference_no": d.payment_reference,
                "description": f"Sale of {a.tag} {a.name}"[:500]})
            d.income_entry_id = income.id
        a.status, a.disposed_on = AssetStatus.DISPOSED, d.disposed_on
        gain = money(d.proceeds) - bv
        await self._event(a, "DISPOSED", f"{d.method.value.replace('_', ' ').title()}: book value {bv:,.2f}, received {money(d.proceeds):,.2f}, "
                                         f"{'gain' if gain >= 0 else 'loss'} {abs(gain):,.2f}", actor_id=actor_id, note=d.reason, on=d.disposed_on)
        await self.audit.log(actor_user_id=actor_id, action="asset.disposal_approved", entity_type="asset_disposal", entity_id=d.id,
                             metadata={"tag": a.tag, "book_value": str(bv), "proceeds": str(d.proceeds), "sole_approver": sole})
        return d

    async def disposals(self, status: str | None = None) -> list[dict]:
        from app.accounts.service import AccountsService

        stmt = select(AssetDisposal, Asset).join(Asset, Asset.id == AssetDisposal.asset_id).order_by(AssetDisposal.created_at.desc()).limit(300)
        if status:
            stmt = stmt.where(AssetDisposal.status == status)
        rows = (await self.session.execute(stmt)).all()
        names = await AccountsService(self.session)._names({d.requested_by for d, _a in rows} | {d.decided_by for d, _a in rows})
        out = []
        for d, a in rows:
            item = self._disposal_dict(d, a, names)
            if d.status == "REQUESTED":
                total, _last = (await self._posted([a.id])).get(a.id, (Decimal(0), None))
                item["book_value_now"] = float(money(a.cost) - money(a.opening_accumulated) - total)
            out.append(item)
        return out

    # ================================================================== physical checks
    async def start_check(self, data: dict, *, actor_id) -> AssetVerification:
        loc = await self._location(data.get("location_id"))
        title = _clean(data.get("title"), 150) or f"Check of {loc.name if loc else 'all assets'} {institute_today():%d %b %Y}"
        v = AssetVerification(title=title, location_id=loc.id if loc else None, status="OPEN", started_by=actor_id, notes=_clean(data.get("notes")))
        self.session.add(v)
        await self.session.flush()
        stmt = select(Asset).where(Asset.status != AssetStatus.DISPOSED)
        if loc:
            stmt = stmt.where(Asset.location_id == loc.id)
        n = 0
        for a in (await self.session.execute(stmt)).scalars():
            self.session.add(AssetVerificationLine(verification_id=v.id, asset_id=a.id, expected=True, expected_location_id=a.location_id, result="NOT_CHECKED"))
            n += 1
        await self.audit.log(actor_user_id=actor_id, action="asset.check_started", entity_type="asset_verification", entity_id=v.id,
                             metadata={"title": title, "expected": n})
        return v

    async def _check(self, verification_id, *, lock: bool = False) -> AssetVerification:
        stmt = select(AssetVerification).where(AssetVerification.id == verification_id)
        if lock:
            stmt = stmt.with_for_update()
        v = (await self.session.execute(stmt)).scalar_one_or_none()
        if v is None:
            raise ResourceNotFoundError("Physical check not found.")
        return v

    async def mark(self, verification_id, data: dict, *, actor_id) -> dict:
        """Record one asset seen (by tag, scanned label, or id) - or, with result MISSING / NOT_CHECKED, set it by hand."""
        v = await self._check(verification_id)
        if v.status != "OPEN":
            raise BusinessRuleViolationError("This check is closed.")
        a = await self._asset(data["asset_id"]) if data.get("asset_id") else await self.by_tag(data.get("tag") or "")
        if a.status == AssetStatus.DISPOSED:
            raise BusinessRuleViolationError(f"{a.tag} was disposed of on {a.disposed_on:%d %b %Y}. It should not be here; tell the administration.")
        found_at = data.get("found_location_id") or v.location_id
        await self._location(found_at)
        line = (await self.session.execute(select(AssetVerificationLine).where(AssetVerificationLine.verification_id == v.id,
                                                                                AssetVerificationLine.asset_id == a.id))).scalar_one_or_none()
        if line is None:
            line = AssetVerificationLine(verification_id=v.id, asset_id=a.id, expected=False, expected_location_id=a.location_id)
            self.session.add(line)
        result = (data.get("result") or "").upper()
        if result in ("MISSING", "NOT_CHECKED"):
            line.result, line.found_location_id = result, None
        elif found_at and line.expected_location_id and str(found_at) != str(line.expected_location_id):
            line.result, line.found_location_id = "WRONG_PLACE", found_at
        elif found_at and not line.expected_location_id:
            line.result, line.found_location_id = "WRONG_PLACE", found_at       # had no place on record: record where it is
        else:
            line.result, line.found_location_id = "FOUND", found_at
        if not line.expected and line.result == "FOUND" and v.location_id:
            line.result = "WRONG_PLACE"
        line.condition = AssetCondition(data["condition"]) if data.get("condition") else line.condition
        line.note = _clean(data.get("note"), 300) or line.note
        line.checked_by, line.checked_at = actor_id, datetime.now(timezone.utc)
        await self.session.flush()
        return {"tag": a.tag, "name": a.name, "result": line.result, "expected": line.expected}

    async def check_detail(self, verification_id) -> dict:
        from app.accounts.service import AccountsService

        v = await self._check(verification_id)
        locs = {loc.id: loc.name for loc in (await self.session.execute(select(AssetLocation))).scalars()}
        rows = (await self.session.execute(select(AssetVerificationLine, Asset).join(Asset, Asset.id == AssetVerificationLine.asset_id)
                                           .where(AssetVerificationLine.verification_id == v.id).order_by(Asset.tag))).all()
        names = await AccountsService(self.session)._names({v.started_by, v.closed_by} | {line.checked_by for line, _a in rows})
        counts: dict[str, int] = {}
        lines = []
        for line, a in rows:
            counts[line.result] = counts.get(line.result, 0) + 1
            lines.append({"asset_id": a.id, "tag": a.tag, "name": a.name, "expected": line.expected, "expected_location": locs.get(line.expected_location_id),
                          "result": line.result, "found_location": locs.get(line.found_location_id), "condition": line.condition.value if line.condition else None,
                          "note": line.note, "checked_by": names.get(line.checked_by), "checked_at": line.checked_at})
        return {"id": v.id, "title": v.title, "location": locs.get(v.location_id) or "All places", "location_id": v.location_id, "status": v.status,
                "started_by": names.get(v.started_by), "started_at": v.created_at, "closed_by": names.get(v.closed_by), "closed_at": v.closed_at,
                "notes": v.notes, "counts": counts, "lines": lines}

    async def close_check(self, verification_id, *, apply_moves: bool, actor_id) -> dict:
        """Close: what was not seen becomes Missing; what was seen updates condition (and place, if asked); found 'missing' assets come back."""
        v = await self._check(verification_id, lock=True)
        if v.status != "OPEN":
            raise BusinessRuleViolationError("This check is already closed.")
        rows = (await self.session.execute(select(AssetVerificationLine, Asset).join(Asset, Asset.id == AssetVerificationLine.asset_id)
                                           .where(AssetVerificationLine.verification_id == v.id))).all()
        out = {"found": 0, "wrong_place": 0, "missing": 0, "moved": 0}
        for line, a in rows:
            if a.status == AssetStatus.DISPOSED:
                continue
            if line.result == "NOT_CHECKED" and line.expected:
                line.result = "MISSING"
            if line.result == "MISSING":
                out["missing"] += 1
                if a.status != AssetStatus.MISSING:
                    await self.update(a.id, {"status": "MISSING", "note": f"Not found in '{v.title}'"}, actor_id=actor_id)
                continue
            if line.result not in ("FOUND", "WRONG_PLACE"):
                continue
            out["found" if line.result == "FOUND" else "wrong_place"] += 1
            changes: dict = {"note": f"Physical check '{v.title}'"}
            if line.condition and line.condition != a.condition:
                changes["condition"] = line.condition.value
            if a.status == AssetStatus.MISSING:
                changes["status"] = "IN_USE"
            if line.result == "WRONG_PLACE" and apply_moves and line.found_location_id:
                changes["location_id"] = line.found_location_id
                out["moved"] += 1
            await self._event(a, "VERIFIED", f"Seen in '{v.title}'" + (" (in the wrong place)" if line.result == "WRONG_PLACE" else ""), actor_id=actor_id)
            if len(changes) > 1:
                await self.update(a.id, changes, actor_id=actor_id)
        v.status, v.closed_by, v.closed_at = "CLOSED", actor_id, datetime.now(timezone.utc)
        await self.audit.log(actor_user_id=actor_id, action="asset.check_closed", entity_type="asset_verification", entity_id=v.id, metadata=out)
        return out

    async def checks(self) -> list[dict]:
        locs = {loc.id: loc.name for loc in (await self.session.execute(select(AssetLocation))).scalars()}
        counts: dict = {}
        for vid, result, n in (await self.session.execute(select(AssetVerificationLine.verification_id, AssetVerificationLine.result, func.count())
                                                          .group_by(AssetVerificationLine.verification_id, AssetVerificationLine.result))).all():
            counts.setdefault(vid, {})[result] = n
        return [{"id": v.id, "title": v.title, "location": locs.get(v.location_id) or "All places", "status": v.status, "started_at": v.created_at,
                 "closed_at": v.closed_at, "counts": counts.get(v.id, {})}
                for v in (await self.session.execute(select(AssetVerification).order_by(AssetVerification.created_at.desc()).limit(100))).scalars()]

    # ================================================================== labels
    async def labels_pdf(self, *, ids: list | None = None, location_id=None) -> bytes:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas

        from app.records.pdf import _qr

        stmt = select(Asset).where(Asset.status != AssetStatus.DISPOSED).order_by(Asset.tag)
        if ids:
            stmt = stmt.where(Asset.id.in_(ids))
        elif location_id:
            stmt = stmt.where(Asset.location_id == location_id)
        assets = (await self.session.execute(stmt.limit(2000))).scalars().all()
        if not assets:
            raise BusinessRuleViolationError("No assets to print labels for.")
        locs = {loc.id: loc.name for loc in (await self.session.execute(select(AssetLocation))).scalars()}
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        c.setTitle("Asset labels")
        cols, rows_per = 3, 8
        w, h = 63.5 * mm, 33.9 * mm                       # common 24-per-sheet label paper; plain paper works too (cut along the lines)
        left, top = (A4[0] - cols * w) / 2, A4[1] - (A4[1] - rows_per * h) / 2
        base = (settings.public_url or "").rstrip("/")
        for i, a in enumerate(assets):
            if i and i % (cols * rows_per) == 0:
                c.showPage()
            k = i % (cols * rows_per)
            x, y = left + (k % cols) * w, top - (k // cols + 1) * h
            c.setStrokeColor(colors.HexColor("#c5cde0"))
            c.setLineWidth(0.3)
            c.rect(x, y, w, h)
            q = h - 6 * mm
            _qr(c, f"{base}/dashboard/assets/tag#tag={a.tag}", x + w - q - 2 * mm, y + 3 * mm, q)
            tx, room = x + 3 * mm, w - q - 7 * mm

            def fit(text: str, font: str, size: float) -> str:
                text = text or ""
                while text and c.stringWidth(text, font, size) > room:
                    text = text[:-2] + "…" if len(text) > 2 else ""
                return text

            c.setFillColor(colors.HexColor("#5b6785"))
            c.setFont("Helvetica", 6)
            c.drawString(tx, y + h - 6 * mm, fit(settings.institute_name or "", "Helvetica", 6))
            c.setFillColor(colors.HexColor("#0f1b3d"))
            size = 11
            while size > 7 and c.stringWidth(a.tag, "Helvetica-Bold", size) > room:
                size -= 0.5
            c.setFont("Helvetica-Bold", size)
            c.drawString(tx, y + h - 12 * mm, a.tag)
            c.setFont("Helvetica", 7)
            c.drawString(tx, y + h - 17 * mm, fit(a.name, "Helvetica", 7))
            c.setFont("Helvetica", 6)
            c.setFillColor(colors.HexColor("#5b6785"))
            c.drawString(tx, y + h - 21 * mm, fit(locs.get(a.location_id) or "", "Helvetica", 6))
            c.drawString(tx, y + 4 * mm, fit("Property of the institute. Do not remove.", "Helvetica", 6))
        c.save()
        return buf.getvalue()
