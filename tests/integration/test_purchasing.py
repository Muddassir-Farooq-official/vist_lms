"""Purchasing: ask, approve, order, receive, pay -- and the payment lands in the accounts as an expense."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.accounts.models import Expense
from app.core.clock import institute_today
from app.core.config import settings
from app.notifications.models import Notification
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_fees import _create_accounts_officer, _create_and_activate_student, _login, _login_admin

pytestmark = pytest.mark.asyncio
P = "/api/v1/purchases"
A = "/api/v1/accounts"
OFFICER_PW = "OfficerReal123!"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_cache: dict = {}


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    _cache.clear()


async def _team(client, db):
    """An accounts officer and two teachers. Returns their logins."""
    officer = await _create_accounts_officer(client, db)
    await _login_admin(client)
    t1 = await _staff(client, db, "T01", "TEACHER", "35202-7400001-1")
    t2 = await _staff(client, db, "T02", "TEACHER", "35202-7400002-2")
    return {"officer": (officer, OFFICER_PW), "t1": t1, "t2": t2}


async def _as(client, team, who):
    username, secret = team[who]
    if who == "officer":
        await _login(client, username, secret)
    elif who not in _cache:
        _cache[who] = await _first_login(client, username, secret)
    else:
        await _login(client, username, _cache[who])


def _body(**kw):
    body = {"title": "Chairs and a table for room 12", "justification": "The old ones are broken and unsafe.",
            "items": [{"description": "Office chair", "quantity": 3, "estimated_unit_price": 4000}, {"description": "Table", "quantity": 1, "estimated_unit_price": 10000}]}
    body.update(kw)
    return body


async def _cat(client, kind, part):
    return next(c for c in (await client.get(f"{A}/categories", params={"kind": kind})).json() if part.lower() in c["name"].lower())


async def _vendor(client, name="Zeeshan Furniture"):
    return (await client.post(f"{A}/vendors", json={"name": name})).json()["id"]


async def _request(client, **kw):
    r = await client.post(P, json=_body(**kw))
    assert r.status_code == 201, r.text
    return r.json()


async def _journey_to(client, team, stage, **req):
    """Drives one purchase as far as `stage` (approved / ordered / received / paid). Returns it and the vendor id."""
    await _as(client, team, "t1")
    po = await _request(client, **req)
    await _login_admin(client)
    assert (await client.post(f"{P}/{po['id']}/approve")).status_code == 200
    if stage == "approved":
        return po, None
    await _as(client, team, "officer")
    vendor = await _vendor(client)
    assert (await client.post(f"{P}/{po['id']}/order", json={"vendor_id": vendor, "expected_delivery": str(institute_today() + timedelta(days=5))})).status_code == 200
    if stage == "ordered":
        return po, vendor
    items = (await client.get(f"{P}/{po['id']}")).json()["items"]
    lines = [{"item_id": items[0]["id"], "received_quantity": 3, "actual_unit_price": 4200}, {"item_id": items[1]["id"], "received_quantity": 1, "actual_unit_price": 9500}]
    assert (await client.post(f"{P}/{po['id']}/receive", json={"invoice_no": "INV-77", "lines": lines})).status_code == 200
    if stage == "received":
        return po, vendor
    paid = await client.post(f"{P}/{po['id']}/pay", data={"payment_method": "CHEQUE", "reference_no": "CHQ-0001", "paid_date": str(institute_today())}, files=[("files", ("invoice.png", PNG, "image/png"))])
    assert paid.status_code == 200, paid.text
    return po, vendor


# ====================================================================== asking
async def test_a_staff_member_asks_for_something_and_the_administration_is_told(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "t1")
    cat = await _cat(app_client, "EXPENSE", "Equipment") if False else None
    po = await _request(app_client)
    assert po["status"] == "REQUESTED" and po["reference"].startswith("PO-") and po["estimated_total"] == 22000.0 and po["actions"] == ["cancel"]
    assert [(i["description"], i["quantity"], i["estimated_total"]) for i in po["items"]] == [("Office chair", 3.0, 12000.0), ("Table", 1.0, 10000.0)] and po["requested_by_name"]
    note = (await db_session.execute(select(Notification).where(Notification.title.like("Purchase request waiting%")))).scalars().first()
    assert "22,000" in note.title and note.link == "/dashboard/admin/finance?open=purchases"
    mine = (await app_client.get(f"{P}/mine")).json()
    assert [m["id"] for m in mine] == [po["id"]]


async def test_a_request_is_checked_and_belongs_to_the_person_who_asked(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _login_admin(app_client)
    system = await _cat(app_client, "EXPENSE", "Purchases")
    income = await _cat(app_client, "INCOME", "Donations")
    inactive_vendor = await _vendor(app_client, "Closed Traders")
    await app_client.patch(f"{A}/vendors/{inactive_vendor}", json={"is_active": False})
    await _as(app_client, team, "t1")
    bad = [({"items": []}, "items"), ({"items": [{"description": "Chair", "quantity": 0, "estimated_unit_price": 100}]}, "quantity"), ({"items": [{"description": "Chair", "quantity": 1, "estimated_unit_price": 0}]}, "estimated_unit_price"),
           ({"title": "ab"}, "title"), ({"needed_by": str(institute_today() - timedelta(days=1))}, "past"), ({"category_id": system["id"]}, "expense category"), ({"category_id": income["id"]}, "expense category"),
           ({"suggested_vendor_id": inactive_vendor}, "active vendor"), ({"items": [{"description": f"Item {i}", "quantity": 1, "estimated_unit_price": 10} for i in range(31)]}, "items")]
    for change, word in bad:
        r = await app_client.post(P, json=_body(**change))
        assert r.status_code == 422 and word in r.text.lower() or word in r.text, (change, r.text)
    po = await _request(app_client)
    await _as(app_client, team, "t2")
    assert (await app_client.get(f"{P}/{po['id']}")).status_code == 404                                    # someone else's request is simply not there
    assert (await app_client.get(f"{P}/mine")).json() == []
    for call in (app_client.get(P), app_client.get(f"{P}/summary"), app_client.post(f"{P}/{po['id']}/approve"), app_client.post(f"{P}/{po['id']}/order", json={"vendor_id": inactive_vendor})):
        assert (await call).status_code == 403
    student = await _create_and_activate_student(app_client, cnic="55555-5555555-5")
    await _login(app_client, student[1], "StudentPass123!")
    assert (await app_client.post(P, json=_body())).status_code == 403                                     # students cannot ask
    app_client.cookies.clear()
    assert (await app_client.get(f"{P}/mine")).status_code == 401


# ====================================================================== the whole journey
async def test_ask_approve_order_receive_pay_and_it_becomes_an_expense(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _login_admin(app_client)
    cat = await _cat(app_client, "EXPENSE", "Equipment")
    await _as(app_client, team, "t1")
    po = await _request(app_client, category_id=cat["id"])
    await _as(app_client, team, "officer")
    assert (await app_client.post(f"{P}/{po['id']}/approve")).status_code == 403                           # the accounts office cannot approve
    await _login_admin(app_client)
    ok = (await app_client.post(f"{P}/{po['id']}/approve")).json()
    assert ok["status"] == "APPROVED" and ok["decided_by_name"] and ok["actions"] == ["order", "cancel"]
    await _as(app_client, team, "officer")
    vendor = await _vendor(app_client)
    ordered = (await app_client.post(f"{P}/{po['id']}/order", json={"vendor_id": vendor, "expected_delivery": str(institute_today() + timedelta(days=5)), "note": "Deliver to the store"})).json()
    assert ordered["status"] == "ORDERED" and ordered["vendor_name"] == "Zeeshan Furniture" and ordered["actions"] == ["receive"]
    items = ordered["items"]
    lines = [{"item_id": items[0]["id"], "received_quantity": 3, "actual_unit_price": 4200}, {"item_id": items[1]["id"], "received_quantity": 1, "actual_unit_price": 9500}]
    got = (await app_client.post(f"{P}/{po['id']}/receive", json={"invoice_no": "INV-77", "lines": lines})).json()
    assert got["status"] == "RECEIVED" and got["actual_total"] == 22100.0 and got["invoice_no"] == "INV-77" and got["over_estimate_percent"] == 0.5 and got["actions"] == ["pay"]
    assert [(i["received_quantity"], i["actual_unit_price"], i["actual_total"]) for i in got["items"]] == [(3.0, 4200.0, 12600.0), (1.0, 9500.0, 9500.0)]
    assert (await app_client.get(f"{P}/summary")).json() == {"waiting_for_approval": 0, "to_order": 0, "on_order": 0, "to_pay": 1, "owed_to_vendors": 22100.0, "paid_total": 0.0}
    paid = (await app_client.post(f"{P}/{po['id']}/pay", data={"payment_method": "CHEQUE", "reference_no": "CHQ-0001", "paid_date": str(institute_today())}, files=[("files", ("invoice.png", PNG, "image/png"))])).json()
    assert paid["status"] == "PAID" and paid["expense_id"] and paid["expense_reference"].startswith("EXP-") and paid["payment_reference"] == "CHQ-0001" and paid["actions"] == []
    # it is now an ordinary approved expense in the books
    exp = (await app_client.get(f"{A}/expenses/{paid['expense_id']}")).json()
    assert (exp["status"], exp["source"], exp["amount"], exp["category_name"], exp["vendor_name"]) == ("APPROVED", "PURCHASE", 22100.0, cat["name"], "Zeeshan Furniture") and exp["title"].startswith("Chairs and a table") and len(exp["attachments"]) == 1
    assert (await app_client.get(f"{A}/summary")).json()["expenses"] == 22100.0
    assert next(v for v in (await app_client.get(f"{A}/vendors")).json() if v["name"] == "Zeeshan Furniture")["total_spent"] == 22100.0
    assert (await app_client.get(f"{P}/summary")).json()["paid_total"] == 22100.0 and (await app_client.get(f"{P}/summary")).json()["owed_to_vendors"] == 0.0
    assert (await app_client.post(f"{A}/expenses/{paid['expense_id']}/void", json={"reason": "wrong entry"})).status_code == 403          # (the officer cannot) ...
    await _login_admin(app_client)
    refused = await app_client.post(f"{A}/expenses/{paid['expense_id']}/void", json={"reason": "wrong entry"})
    assert refused.status_code == 422 and "purchase" in refused.text                                                                       # ... and even the administration reverses it from the purchase
    # the person who asked was kept informed at each step
    titles = [n.title.split(":")[0] for n in (await db_session.execute(select(Notification).order_by(Notification.created_at))).scalars() if n.user_id != admin_user.id]
    assert {"Purchase approved", "Ordered", "Arrived", "Approved, ready to order"} <= set(titles)


async def test_the_wrong_step_at_the_wrong_time_is_refused(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "t1")
    po = await _request(app_client)
    await _as(app_client, team, "officer")
    vendor = await _vendor(app_client)
    step = lambda path, **kw: app_client.post(f"{P}/{po['id']}/{path}", **kw)                                                           # noqa: E731
    assert (await step("order", json={"vendor_id": vendor})).status_code == 422                                                         # not approved yet
    assert (await step("receive", json={"invoice_no": "X", "lines": [{"item_id": po["items"][0]["id"], "received_quantity": 1, "actual_unit_price": 5}]})).status_code == 422
    assert (await step("pay", data={"payment_method": "CASH", "paid_date": str(institute_today())})).status_code == 422
    await _login_admin(app_client)
    assert (await step("reject", json={"reason": "no"})).status_code == 422                                                              # a real reason
    assert (await step("reject", json={"reason": "We already have enough chairs"})).json()["status"] == "REJECTED"
    assert (await step("approve")).status_code == 422 and (await step("cancel", json={"reason": "later"})).status_code == 422             # a closed request stays closed
    t = [n for n in (await db_session.execute(select(Notification).where(Notification.title.like("Purchase rejected%")))).scalars()]
    assert t and "enough chairs" in t[0].message


async def test_cancelling_follows_who_may_and_when(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _as(app_client, team, "t1")
    first = await _request(app_client)
    second = await _request(app_client, title="A second request")
    cancel = lambda i, **kw: app_client.post(f"{P}/{i}/cancel", json=kw)                                                                 # noqa: E731
    assert (await cancel(first["id"])).json()["status"] == "CANCELLED"                                                                   # the person who asked, before it is decided
    await _as(app_client, team, "t2")
    assert (await cancel(second["id"])).status_code == 403                                                                               # not someone else's
    await _login_admin(app_client)
    assert (await app_client.post(f"{P}/{second['id']}/approve")).status_code == 200
    await _as(app_client, team, "t1")
    assert (await cancel(second["id"])).status_code == 422                                                                               # after the decision, only the administration
    await _login_admin(app_client)
    assert (await cancel(second["id"], reason="x")).status_code == 422
    done = (await cancel(second["id"], reason="Budget was frozen")).json()
    assert done["status"] == "CANCELLED" and done["cancel_reason"] == "Budget was frozen"
    told = (await db_session.execute(select(Notification).where(Notification.title.like("Purchase cancelled%")))).scalars().all()
    assert len(told) == 1 and "frozen" in told[0].message                                                                                  # told only when someone else cancelled it
    received, _ = await _journey_to(app_client, team, "received", title="Another one")
    await _login_admin(app_client)
    assert (await cancel(received["id"], reason="Too late")).status_code == 422                                                           # goods already arrived


async def test_receiving_is_checked_line_by_line(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    po, _vendor_id = await _journey_to(app_client, team, "ordered")
    items = (await app_client.get(f"{P}/{po['id']}")).json()["items"]
    step = lambda lines, inv="INV-1": app_client.post(f"{P}/{po['id']}/receive", json={"invoice_no": inv, "lines": lines})               # noqa: E731
    ln = lambda i, q, p: {"item_id": items[i]["id"], "received_quantity": q, "actual_unit_price": p}                                       # noqa: E731
    r = await step([ln(0, 4, 100)]); assert r.status_code == 422 and "more than the 3" in r.text                                             # cannot receive more than ordered
    r = await step([ln(0, 2, 0)]); assert r.status_code == 422 and "price actually charged" in r.text
    r = await step([ln(0, 0, 100), ln(1, 0, 100)]); assert r.status_code == 422 and "Nothing was received" in r.text
    r = await step([{"item_id": "00000000-0000-0000-0000-000000000000", "received_quantity": 1, "actual_unit_price": 5}]); assert r.status_code == 422 and "not part of this order" in r.text
    r = await step([ln(0, 1, 100), ln(0, 1, 100)]); assert r.status_code == 422 and "twice" in r.text
    assert (await app_client.post(f"{P}/{po['id']}/receive", json={"invoice_no": "", "lines": [ln(0, 1, 100)]})).status_code == 422          # an invoice number is required
    assert (await app_client.get(f"{P}/{po['id']}")).json()["status"] == "ORDERED"                                                         # nothing above changed it
    partial = (await step([ln(0, 2, 4100)], "INV-PART")).json()                                                                              # only 2 chairs came; the table did not
    assert partial["status"] == "RECEIVED" and partial["actual_total"] == 8200.0 and partial["items"][1]["received_quantity"] == 0.0


async def test_paying_is_checked_and_a_failure_creates_nothing(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    po, _v = await _journey_to(app_client, team, "received")
    today = institute_today()
    pay = lambda data, files=None: app_client.post(f"{P}/{po['id']}/pay", data=data, files=files or None)                                  # noqa: E731
    r = await pay({"payment_method": "CHEQUE", "paid_date": str(today)}); assert r.status_code == 422 and "cheque number" in r.text
    r = await pay({"payment_method": "CASH", "paid_date": str(today + timedelta(days=2))}); assert r.status_code == 422 and "future" in r.text
    r = await pay({"payment_method": "CASH", "paid_date": str(today)}, [("files", ("bad.exe", b"MZ" + b"0" * 40, "application/octet-stream"))]); assert r.status_code == 422 and "bad.exe" in r.text
    r = await pay({"payment_method": "CASH", "paid_date": str(today)}, [("files", (f"r{i}.png", PNG, "image/png")) for i in range(6)]); assert r.status_code == 422 and "at most 5" in r.text
    assert (await app_client.get(f"{P}/{po['id']}")).json()["status"] == "RECEIVED"
    assert (await db_session.execute(select(func.count()).select_from(Expense))).scalar_one() == 0                                        # no half-created expense
    ok = await pay({"payment_method": "CASH", "paid_date": str(today)})
    assert ok.status_code == 200 and ok.json()["status"] == "PAID"
    assert (await pay({"payment_method": "CASH", "paid_date": str(today)})).status_code == 422                                              # not paid twice
    assert (await db_session.execute(select(func.count()).select_from(Expense))).scalar_one() == 1


async def test_the_administration_can_reverse_a_payment_made_by_mistake(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    po, _v = await _journey_to(app_client, team, "paid")
    assert (await app_client.post(f"{P}/{po['id']}/reverse-payment", json={"reason": "Wrong account used"})).status_code == 403            # the officer cannot
    await _login_admin(app_client)
    assert (await app_client.post(f"{P}/{po['id']}/reverse-payment", json={"reason": "x"})).status_code == 422
    expense_id = (await app_client.get(f"{P}/{po['id']}")).json()["expense_id"]
    back = (await app_client.post(f"{P}/{po['id']}/reverse-payment", json={"reason": "Wrong account used"})).json()
    assert back["status"] == "RECEIVED" and back["expense_id"] is None and back["actions"] == ["pay"] and back["invoice_no"] == "INV-77"
    exp = (await app_client.get(f"{A}/expenses/{expense_id}")).json()
    assert exp["status"] == "VOIDED" and "Wrong account used" in exp["void_reason"]
    assert (await app_client.get(f"{A}/summary")).json()["expenses"] == 0.0                                                                  # no longer counted
    assert (await app_client.get(f"{P}/summary")).json()["owed_to_vendors"] == 22100.0                                                      # owed to the vendor again
    again = await app_client.post(f"{P}/{po['id']}/pay", data={"payment_method": "CASH", "paid_date": str(institute_today())})
    assert again.status_code == 200 and again.json()["status"] == "PAID"
    assert (await app_client.get(f"{A}/summary")).json()["expenses"] == 22100.0
    assert (await app_client.post(f"{P}/{po['id']}/reverse-payment", json={"reason": "Not paid yet"})).status_code == 200
    assert (await app_client.post(f"{P}/{po['id']}/reverse-payment", json={"reason": "Not paid anymore"})).status_code == 422             # only a paid one can be reversed