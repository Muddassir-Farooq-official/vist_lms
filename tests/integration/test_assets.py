"""Fixed-asset register: depreciation arithmetic, monthly runs and their reversal, assets owned before the system,
disposal with approval (book value, income, gain / loss), physical checks, CSV import, purchases, labels, reports."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.assets import depreciation as dep
from app.core.clock import institute_today
from tests.integration.test_fees import _login_admin
from tests.integration.test_purchasing import _as, _journey_to, _quiet, _team  # noqa: F401  (_quiet is an autouse fixture)

pytestmark = pytest.mark.asyncio
A = "/api/v1/assets"
R = "/api/v1/report-center"


def _months_back(d: date, n: int) -> date:
    y, m = d.year, d.month - n
    while m < 1:
        y, m = y - 1, m + 12
    return date(y, m, 1)


# ====================================================================== arithmetic (no database)
async def test_depreciation_arithmetic():
    sl = dep.Terms("STRAIGHT_LINE", Decimal("120000"), Decimal("0"), Decimal("10"), None)
    assert dep.charge(sl, Decimal("120000"), date(2026, 1, 1), date(2026, 1, 31)) == Decimal("1000.00")       # a full month = 1/12 of a year
    assert dep.charge(sl, Decimal("120000"), date(2026, 2, 1), date(2026, 2, 28)) == Decimal("1000.00")       # February too
    assert dep.charge(sl, Decimal("120000"), date(2026, 1, 16), date(2026, 1, 31)) == Decimal("516.13")       # 16 of 31 days
    assert dep.charge(sl, Decimal("500"), date(2026, 1, 1), date(2026, 12, 31)) == Decimal("500.00")          # never below the residual
    rb = dep.Terms("REDUCING_BALANCE", Decimal("100000"), Decimal("0"), None, Decimal("30"))
    assert dep.charge(rb, Decimal("100000"), date(2025, 7, 1), date(2026, 6, 30)) == Decimal("30000.00")      # 30% of what is left
    assert dep.charge(rb, Decimal("70000"), date(2026, 7, 1), date(2027, 6, 30)) == Decimal("21000.00")
    assert dep.used_before(sl, date(2020, 7, 1), date(2023, 7, 1)) == Decimal("36000.00")                     # 3 years used before go-live
    land = dep.Terms("NONE", Decimal("5000000"), Decimal("0"), None, None)
    assert dep.charge(land, Decimal("5000000"), date(2020, 1, 1), date(2026, 1, 1)) == 0


# ====================================================================== helpers
async def _setup(client):
    await _login_admin(client)
    added = (await client.post(f"{A}/categories/suggested")).json()["added"]
    assert "Computers and IT equipment" in added and "Land" in added
    assert (await client.post(f"{A}/categories/suggested")).json()["added"] == []                       # only once
    cat = (await client.post(f"{A}/categories", json={"code": "TST", "name": "Test equipment", "method": "STRAIGHT_LINE", "useful_life_years": 10})).json()
    lab = (await client.post(f"{A}/locations", json={"name": "Computer Lab 1", "building": "Main"})).json()
    room = (await client.post(f"{A}/locations", json={"name": "Room 12"})).json()
    cats = {c["code"]: c for c in (await client.get(f"{A}/categories")).json()}
    return cat["id"], cats, lab["id"], room["id"]


async def _add(client, **kw):
    r = await client.post(A, json=kw)
    assert r.status_code == 201, r.text
    return r.json()


# ====================================================================== runs
async def test_monthly_runs_reverse_only_the_latest_and_book_value(app_client, admin_user):
    tst, _cats, lab, _room = await _setup(app_client)
    today = institute_today()
    this_month = today.replace(day=1)
    bought = _months_back(this_month, 3)
    a = (await _add(app_client, name="Server rack", category_id=tst, cost=120000, acquired_on=str(bought), location_id=lab))[0]
    assert a["tag"] == "VIST-TST-0001"
    two = await _add(app_client, name="Desktop", category_id=tst, cost=60000, acquired_on=str(bought), quantity=2, location_id=lab)
    assert [x["tag"] for x in two] == ["VIST-TST-0002", "VIST-TST-0003"]                                  # each unit its own tag

    end1 = dep.month_end(_months_back(this_month, 2))
    pv = (await app_client.post(f"{A}/depreciation/preview", json={"period_end": str(end1)})).json()
    assert pv["total"] == 4000.0                                                                          # 2 months: 2,000 + 2 x 1,000
    bad = await app_client.post(f"{A}/depreciation/runs", json={"period_end": str(end1 - timedelta(days=3))})
    assert bad.status_code == 422 and "last day of a month" in bad.text
    future = await app_client.post(f"{A}/depreciation/runs", json={"period_end": str(dep.month_end(dep.month_end(this_month) + timedelta(days=1)))})
    assert future.status_code == 422
    run1 = (await app_client.post(f"{A}/depreciation/runs", json={"period_end": str(end1)})).json()
    assert run1["total"] == 4000.0 and run1["assets"] == 3
    again = await app_client.post(f"{A}/depreciation/runs", json={"period_end": str(end1)})
    assert again.status_code == 422 and "already posted" in again.text
    end2 = dep.month_end(_months_back(this_month, 1))
    run2 = (await app_client.post(f"{A}/depreciation/runs", json={"period_end": str(end2)})).json()
    assert run2["total"] == 2000.0

    d = (await app_client.get(f"{A}/{a['id']}")).json()
    assert d["depreciation"]["book_value"] == 117000.0 and d["depreciation"]["posted"] == 3000.0 and d["depreciation"]["depreciated_to"] == str(end2)
    assert d["depreciation"]["projection"][0]["depreciation"] == 12000.0
    assert [e["kind"] for e in d["events"]] == ["ADDED"]

    old = await app_client.post(f"{A}/depreciation/runs/{run1['id']}/reverse", json={"reason": "wrong month posted"})
    assert old.status_code == 422 and "latest" in old.text
    assert (await app_client.post(f"{A}/depreciation/runs/{run2['id']}/reverse", json={"reason": "entered the wrong rate"})).status_code == 200
    assert (await app_client.get(f"{A}/{a['id']}")).json()["depreciation"]["book_value"] == 118000.0
    redo = await app_client.post(f"{A}/depreciation/runs", json={"period_end": str(end2)})                # the month can be run again
    assert redo.status_code == 201 and redo.json()["total"] == 2000.0
    runs = (await app_client.get(f"{A}/depreciation/runs")).json()
    assert [r["reversed"] for r in runs] == [False, True, False] and runs[0]["can_reverse"]

    fixed = await app_client.post(f"{A}/{a['id']}/correct", json={"cost": 100000, "reason": "Invoice shows a lower price"})
    assert fixed.status_code == 422 and "already been posted" in fixed.text                               # posted figures are fixed

    s = (await app_client.get(f"{A}/summary")).json()
    assert s["assets"] == 3 and s["cost"] == 240000.0 and s["book_value"] == 234000.0 and s["depreciated_to"] == str(end2)


async def test_closed_books_block_a_run(app_client, admin_user, db_session):
    from app.accounts.period import set_closed_through

    tst, _cats, _lab, _room = await _setup(app_client)
    this_month = institute_today().replace(day=1)
    await _add(app_client, name="Printer", category_id=tst, cost=12000, acquired_on=str(_months_back(this_month, 2)))
    end = dep.month_end(_months_back(this_month, 1))
    await set_closed_through(db_session, end, actor_id=admin_user.id, reason=None)
    await db_session.commit()
    r = await app_client.post(f"{A}/depreciation/runs", json={"period_end": str(end)})
    assert r.status_code == 422 and "books are closed" in r.text


# ====================================================================== owned before the system
async def test_assets_owned_before_the_system(app_client, admin_user):
    _tst, cats, lab, _room = await _setup(app_client)
    go_live = institute_today().replace(day=1)
    two_years_ago = go_live.replace(year=go_live.year - 2)
    known = (await _add(app_client, name="Old projector", category_id=cats["IT"]["id"], source="OPENING", acquired_on=str(two_years_ago),
                        cost=100000, depreciation_from=str(go_live), location_id=lab))[0]
    d = (await app_client.get(f"{A}/{known['id']}")).json()
    assert d["depreciation"]["opening_accumulated"] == 51000.0 and d["depreciation"]["book_value"] == 49000.0     # 100,000 x (1 - 0.7^2)
    assert "Owned before the system" in d["events"][0]["summary"]

    guess = (await _add(app_client, name="Almirah", category_id=cats["FURN"]["id"], source="OPENING", cost=15000, cost_is_estimate=True))[0]
    g = (await app_client.get(f"{A}/{guess['id']}")).json()
    assert g["depreciation"]["opening_accumulated"] == 0.0 and g["depreciation"]["starts"] == str(go_live) and g["cost_is_estimate"]

    neither = await app_client.post(A, json={"name": "Mystery table", "category_id": cats["FURN"]["id"], "source": "OPENING", "cost": 5000})
    assert neither.status_code == 422 and "estimate" in neither.text
    own = await app_client.post(A, json={"name": "Chair", "category_id": cats["FURN"]["id"], "source": "OPENING", "cost": 5000, "cost_is_estimate": True, "tag": "OLD-117"})
    assert own.status_code == 201 and own.json()[0]["tag"] == "OLD-117"                                          # a number already painted on it
    dup = await app_client.post(A, json={"name": "Chair 2", "category_id": cats["FURN"]["id"], "source": "OPENING", "cost": 5000, "cost_is_estimate": True, "tag": "old-117"})
    assert dup.status_code == 409


async def test_csv_import_checks_everything_first(app_client, admin_user):
    _tst, _cats, _lab, _room = await _setup(app_client)
    head = "tag,name,category,location,department,custodian_employee_code,make_model,serial_number,acquired_on,cost,cost_is_estimate,condition,useful_life_years,rate_percent,residual_value,notes\n"
    bad = head + ",Steel almirah,FURN,Principal's office,,,,,01/08/2015,18000,no,FAIR,,,,\n,Bad row,NOPE,,,,,,,abc,,GOOD,,,,\n"
    r = (await app_client.post(f"{A}/import", json={"csv": bad, "commit": True})).json()
    assert r["imported"] == 0 and r["good"] == 1 and r["errors"][0]["row"] == 3 and len(r["errors"][0]["problems"]) == 2
    assert (await app_client.get(A)).json()["total"] == 0                                                        # nothing written
    good = head + ",Steel almirah,FURN,Principal's office,,,,,01/08/2015,18000,no,FAIR,,,,\n,Projector,IT,Room 12,,,Epson,,,25000,yes,GOOD,,,,value today\n"
    dry = (await app_client.post(f"{A}/import", json={"csv": good})).json()
    assert dry["good"] == 2 and dry["imported"] == 0 and dry["new_places"] == ["Principal's office"]
    done = (await app_client.post(f"{A}/import", json={"csv": good, "commit": True})).json()
    assert done["imported"] == 2
    items = (await app_client.get(A)).json()["items"]
    assert {i["location"] for i in items} == {"Principal's office", "Room 12"}
    almirah = next(i for i in items if i["name"] == "Steel almirah")
    assert almirah["accumulated"] > 0 and almirah["tag"].startswith("VIST-FURN-")                                # 11 years of 15% already used up


# ====================================================================== disposal
async def test_disposal_needs_approval_and_books_the_sale(app_client, admin_user, db_session):
    from app.accounts.models import IncomeEntry

    team = await _team(app_client, db_session)
    tst, _cats, lab, _room = await _setup(app_client)
    this_month = institute_today().replace(day=1)
    a = (await _add(app_client, name="Generator", category_id=tst, cost=120000, acquired_on=str(_months_back(this_month, 3)), location_id=lab))[0]
    end = dep.month_end(_months_back(this_month, 1))
    assert (await app_client.post(f"{A}/depreciation/runs", json={"period_end": str(end)})).status_code == 201       # 3 months: 3,000

    await _as(app_client, team, "officer")
    today = institute_today()
    no_ref = await app_client.post(f"{A}/{a['id']}/disposal", json={"method": "SOLD", "proceeds": 50000, "payment_method": "CHEQUE", "reason": "Replaced by a new one"})
    assert no_ref.status_code == 422
    req = await app_client.post(f"{A}/{a['id']}/disposal", json={"method": "SOLD", "proceeds": 50000, "payment_method": "CASH", "buyer": "Ali Traders",
                                                                  "reason": "Replaced by a new one", "disposed_on": str(today)})
    assert req.status_code == 201
    assert (await app_client.post(f"{A}/{a['id']}/disposal", json={"method": "SCRAPPED", "reason": "again"})).status_code == 422
    assert (await app_client.post(f"{A}/disposals/{req.json()['id']}/decide", json={"approve": True})).status_code == 403     # the office cannot approve
    assert (await app_client.post(f"{A}/depreciation/runs/x/reverse", json={"reason": "not allowed here"})).status_code in (403, 422)

    await _login_admin(app_client)
    pending = (await app_client.get(f"{A}/disposals", params={"status": "REQUESTED"})).json()
    assert pending[0]["book_value_now"] == 117000.0
    ok = await app_client.post(f"{A}/disposals/{req.json()['id']}/decide", json={"approve": True})
    assert ok.status_code == 200, ok.text
    terms = dep.Terms("STRAIGHT_LINE", Decimal("120000"), Decimal("0"), Decimal("10"), None)
    final = dep.charge(terms, Decimal("117000"), this_month, today)                                                  # this month up to the sale
    assert ok.json()["book_value"] == float(Decimal("117000") - final)
    d = (await app_client.get(f"{A}/{a['id']}")).json()
    assert d["status"] == "DISPOSED" and d["disposals"][0]["gain_or_loss"] == float(Decimal("50000") - (Decimal("117000") - final))
    assert d["entries"][-1]["disposal"] and d["events"][-1]["kind"] == "DISPOSED"
    inc = (await db_session.execute(select(IncomeEntry).where(IncomeEntry.description.like("Sale of VIST-TST-%")))).scalars().all()
    assert len(inc) == 1 and float(inc[0].amount) == 50000.0 and inc[0].received_from == "Ali Traders"
    assert (await app_client.patch(f"{A}/{a['id']}", json={"condition": "POOR"})).status_code == 422                 # off the books now
    runs = (await app_client.get(f"{A}/depreciation/runs")).json()
    undo = await app_client.post(f"{A}/depreciation/runs/{runs[0]['id']}/reverse", json={"reason": "trying to undo it"})
    assert undo.status_code == 422 and "disposed of after" in undo.text                                           # the sale was booked on top of it

    ds = (await app_client.get(f"{R}/run/assets.disposals")).json()
    assert ds["totals"]["proceeds"] == 50000.0 and ds["rows"][0]["approved"]
    inc_st = (await app_client.get(f"{R}/run/accounts.income_statement")).json()
    lines = {r["line"]: r for r in inc_st["rows"]}
    assert lines["Less: book value of assets disposed of"]["total"] == -ok.json()["book_value"]
    assert lines["Sale of fixed assets"]["total"] == 50000.0
    adj = sum(r["total"] for r in inc_st["rows"] if r["section"] == "Fixed assets")
    assert round(lines["Surplus / (deficit) (cash basis)"]["total"] + adj, 2) == lines["Surplus / (deficit) after depreciation"]["total"]


async def test_rejecting_a_disposal_needs_a_reason(app_client, admin_user):
    tst, _cats, _lab, _room = await _setup(app_client)
    a = (await _add(app_client, name="Fan", category_id=tst, cost=5000, acquired_on=str(institute_today())))[0]
    req = (await app_client.post(f"{A}/{a['id']}/disposal", json={"method": "SCRAPPED", "reason": "Motor burnt"})).json()
    assert (await app_client.post(f"{A}/disposals/{req['id']}/decide", json={"approve": False})).status_code == 422
    r = await app_client.post(f"{A}/disposals/{req['id']}/decide", json={"approve": False, "note": "Get it repaired first"})
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    assert (await app_client.get(f"{A}/{a['id']}")).json()["status"] == "IN_USE"


# ====================================================================== moving, repairs, physical check
async def test_history_and_physical_check(app_client, admin_user):
    tst, _cats, lab, room = await _setup(app_client)
    today = str(institute_today())
    a, b = await _add(app_client, name="Monitor", category_id=tst, cost=30000, acquired_on=today, location_id=lab, quantity=2)
    c = (await _add(app_client, name="Laptop", category_id=tst, cost=90000, acquired_on=today, location_id=room))[0]
    assert (await app_client.patch(f"{A}/{a['id']}", json={"condition": "FAIR", "note": "Scratched screen"})).status_code == 200
    assert (await app_client.post(f"{A}/{a['id']}/maintenance", json={"summary": "Replaced power cable", "cost": 800, "vendor_name": "Hafeez Electronics",
                                                                        "status": "IN_USE"})).status_code == 201
    kinds = [e["kind"] for e in (await app_client.get(f"{A}/{a['id']}")).json()["events"]]
    assert kinds == ["ADDED", "CONDITION", "REPAIR"]
    assert (await app_client.patch(f"{A}/{a['id']}", json={"status": "DISPOSED"})).status_code == 422

    chk = (await app_client.post(f"{A}/checks", json={"location_id": lab})).json()
    found = await app_client.post(f"{A}/checks/{chk['id']}/mark", json={"tag": f"https://lms.example/dashboard/assets/tag#tag={a['tag']}", "condition": "POOR"})
    assert found.json()["result"] == "FOUND"
    stray = (await app_client.post(f"{A}/checks/{chk['id']}/mark", json={"tag": c["tag"].lower()})).json()
    assert stray["result"] == "WRONG_PLACE" and stray["expected"] is False                                        # the laptop from Room 12 was in the lab
    assert (await app_client.post(f"{A}/checks/{chk['id']}/mark", json={"tag": "VIST-NOPE-9999"})).status_code == 404
    closed = (await app_client.post(f"{A}/checks/{chk['id']}/close", json={"apply_moves": True})).json()
    assert closed == {"found": 1, "wrong_place": 1, "missing": 1, "moved": 1}
    assert (await app_client.get(f"{A}/{b['id']}")).json()["status"] == "MISSING"
    assert (await app_client.get(f"{A}/{c['id']}")).json()["location"] == "Computer Lab 1"
    assert (await app_client.get(f"{A}/{a['id']}")).json()["condition"] == "POOR"
    assert (await app_client.post(f"{A}/checks/{chk['id']}/mark", json={"tag": a["tag"]})).status_code == 422       # closed

    rep = (await app_client.get(f"{R}/run/assets.verification", params={"check_id": chk["id"]})).json()
    assert rep["rows"][0]["result"] == "Missing" and len(rep["rows"]) == 3
    mt = (await app_client.get(f"{R}/run/assets.maintenance")).json()
    assert mt["totals"]["cost"] == 800.0

    pdf = await app_client.get(f"{A}/labels.pdf", params={"location_id": lab})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert (await app_client.get(f"{A}/lookup", params={"tag": c["tag"]})).json()["id"] == c["id"]


# ====================================================================== purchases
async def test_received_purchase_goes_into_the_register_not_twice(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    po, _vendor = await _journey_to(app_client, team, "paid")
    await _login_admin(app_client)
    _tst, cats, lab, _room = await _setup(app_client)
    waiting = (await app_client.get(f"{A}/from-purchases")).json()
    chairs = next(line for line in waiting[0]["lines"] if line["description"] == "Office chair")
    assert (chairs["left"], chairs["unit_cost"]) == (3, 4200.0)
    made = await app_client.post(f"{A}/from-purchases", json={"order_id": po["id"], "purchase_item_id": chairs["purchase_item_id"], "quantity": 2,
                                                               "category_id": cats["FURN"]["id"], "location_id": lab})
    assert made.status_code == 201 and len(made.json()) == 2
    over = await app_client.post(f"{A}/from-purchases", json={"order_id": po["id"], "purchase_item_id": chairs["purchase_item_id"], "quantity": 2,
                                                               "category_id": cats["FURN"]["id"]})
    assert over.status_code == 422 and "1 left" in over.text
    store = (await app_client.get("/api/v1/inventory/purchases-to-stock")).json()                              # the store sees only what is left
    left = next(line for line in store[0]["lines"] if line["description"] == "Office chair")
    assert left["left_to_stock"] == 1.0
    d = (await app_client.get(f"{A}/{made.json()[0]['id']}")).json()
    assert d["cost"] == 4200.0 and d["invoice_no"] == "INV-77" and d["vendor"] and d["purchase_order_id"] == po["id"]

    inc = (await app_client.get(f"{R}/run/accounts.income_statement")).json()
    addback = next(r for r in inc["rows"] if r["line"].startswith("Add back"))
    assert addback["total"] == 8400.0                                                                           # the two chairs, already expensed


# ====================================================================== who can do what, reports agree
async def test_permissions_and_reports_agree(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    tst, _cats, lab, _room = await _setup(app_client)
    this_month = institute_today().replace(day=1)
    await _add(app_client, name="Photocopier", category_id=tst, cost=240000, acquired_on=str(_months_back(this_month, 2)), location_id=lab)
    await _add(app_client, name="Water cooler", category_id=tst, cost=36000, acquired_on=str(this_month))
    end = dep.month_end(_months_back(this_month, 1))
    await _as(app_client, team, "officer")
    run = await app_client.post(f"{A}/depreciation/runs", json={"period_end": str(end)})                        # the office posts depreciation
    assert run.status_code == 201
    assert (await app_client.post(f"{A}/depreciation/runs/{run.json()['id']}/reverse", json={"reason": "office cannot do this"})).status_code == 403
    assert (await app_client.post(f"{A}/categories", json={"code": "ZZ", "name": "Zed", "method": "NONE"})).status_code == 201
    cat = (await app_client.get(f"{R}/catalog")).json()
    keys = {r["key"] for g in cat["groups"] for r in g["reports"]}
    assert {"assets.register", "assets.schedule", "assets.disposals"} <= keys

    await _login_admin(app_client)
    reg = (await app_client.get(f"{R}/run/assets.register")).json()
    summ = (await app_client.get(f"{A}/summary")).json()
    assert reg["totals"]["book_value"] == summ["book_value"] == 276000.0 - 4000.0                               # 2 months of 2,000
    sch = (await app_client.get(f"{R}/run/assets.schedule")).json()
    t = sch["totals"]
    assert round(t["opening"] + t["added"] + t["brought_in"] - t["depreciation"] - t["disposals"], 2) == t["closing"]
    if this_month.month != 7 and _months_back(this_month, 2).month != 7:                                         # whole history inside one financial year
        assert t["closing"] == reg["totals"]["book_value"]
    past = (await app_client.get(f"{R}/run/assets.register", params={"as_of": str(this_month - timedelta(days=1))})).json()
    assert len(past["rows"]) == 1 and past["totals"]["book_value"] == 236000.0                                   # the cooler came later
    csv = await app_client.get(f"{R}/export/assets.register")
    assert csv.status_code == 200 and "VIST-TST-0001" in csv.text
