"""Inventory: purchases into stock, issues (free / paid now / added to dues), returns, corrections, reversals,
and that every step reaches accounts and the audit trail."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.audit.models import AuditLog
from tests.integration.test_fees import _create_and_activate_student, _login_admin
from tests.integration.test_purchasing import _as, _journey_to, _quiet, _team  # noqa: F401  (_quiet is an autouse fixture)

pytestmark = pytest.mark.asyncio
I = "/api/v1/inventory"


async def _item(client, **kw):
    body = {"name": "A4 paper ream", "category": "Stationery", "unit": "ream", "sale_price": 1200, "reorder_level": 5}
    body.update(kw)
    r = await client.post(f"{I}/items", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def test_purchase_goes_into_stock_at_its_real_cost(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    po, _ = await _journey_to(app_client, team, "received")
    await _as(app_client, team, "officer")
    waiting = (await app_client.get(f"{I}/purchases-to-stock")).json()
    assert [o["order_id"] for o in waiting] == [po["id"]]
    chairs, table = waiting[0]["lines"]
    assert (chairs["left_to_stock"], chairs["unit_cost"]) == (3.0, 4200.0)

    r = await app_client.post(f"{I}/purchases/{po['id']}/receive", json={"lines": [
        {"purchase_item_id": chairs["purchase_item_id"], "quantity": 2, "new_item": {"name": "Office chair", "category": "Furniture", "is_returnable": True}}]})
    assert r.status_code == 201, r.text
    chair_id = r.json()[0]["item_id"]
    over = await app_client.post(f"{I}/purchases/{po['id']}/receive", json={"lines": [{"purchase_item_id": chairs["purchase_item_id"], "quantity": 2, "stock_item_id": chair_id}]})
    assert over.status_code == 422 and "only 1" in over.json()["error"]["message"]
    assert (await app_client.post(f"{I}/purchases/{po['id']}/receive", json={"lines": [{"purchase_item_id": chairs["purchase_item_id"], "stock_item_id": chair_id}]})).status_code == 201

    item = next(i for i in (await app_client.get(f"{I}/items")).json() if i["id"] == chair_id)
    assert (item["quantity_on_hand"], item["average_cost"], item["stock_value"]) == (3.0, 4200.0, 12600.0)
    left = (await app_client.get(f"{I}/purchases-to-stock")).json()
    assert [line["description"] for line in left[0]["lines"]] == ["Table"]


async def test_issue_free_paid_and_to_dues(app_client, admin_user, db_session):
    sid, _ = await _create_and_activate_student(app_client, cnic="71001-0000001-1")
    await _login_admin(app_client)
    paper = await _item(app_client, opening_quantity=20, opening_unit_cost=900)
    assert (paper["quantity_on_hand"], paper["average_cost"], paper["code"].startswith("STK-")) == (20.0, 900.0, True)

    free = await app_client.post(f"{I}/issue", json={"item_id": paper["id"], "quantity": 4, "party_type": "DEPARTMENT", "department": "Computer Lab"})
    assert free.status_code == 201 and free.json()["charge_mode"] == "FREE" and free.json()["balance_after"] == 16.0
    charged_dept = await app_client.post(f"{I}/issue", json={"item_id": paper["id"], "quantity": 1, "party_type": "DEPARTMENT", "department": "Lab", "charge_mode": "PAID_NOW"})
    assert charged_dept.status_code == 422

    # paid at the counter: a receipt, and Store sales income in the books
    sale = await app_client.post(f"{I}/issue", json={"item_id": paper["id"], "quantity": 2, "party_type": "STUDENT", "party_id": sid, "charge_mode": "PAID_NOW", "payment_method": "CASH"})
    assert sale.status_code == 201, sale.text
    s = sale.json()
    assert s["charge_amount"] == 2400.0 and s["receipt_number"].startswith("STORE-")
    pdf = await app_client.get(f"{I}/movements/{s['id']}/receipt.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    book = (await app_client.get("/api/v1/accounts/book", params={"kind": "INCOME"})).json()
    assert any(b["category"] == "Store sales" and b["amount"] == 2400.0 for b in book)
    no_ref = await app_client.post(f"{I}/issue", json={"item_id": paper["id"], "quantity": 1, "party_type": "STUDENT", "party_id": sid, "charge_mode": "PAID_NOW", "payment_method": "CHEQUE"})
    assert no_ref.status_code == 422

    # added to dues: a pending fine the student pays later
    dues = await app_client.post(f"{I}/issue", json={"item_id": paper["id"], "quantity": 1, "party_type": "STUDENT", "party_id": sid, "charge_mode": "ADD_TO_DUES", "unit_price": 1500})
    assert dues.status_code == 201 and dues.json()["fine_id"]
    fines = (await app_client.get("/api/v1/fines", params={"source": "INVENTORY"})).json()
    assert [(f["amount"], f["status"]) for f in fines] == [(1500.0, "PENDING")]

    too_many = await app_client.post(f"{I}/issue", json={"item_id": paper["id"], "quantity": 999, "party_type": "DEPARTMENT", "department": "Lab"})
    assert too_many.status_code == 422 and "in stock" in too_many.json()["error"]["message"]
    summary = (await app_client.get(f"{I}/summary")).json()
    assert summary["month"]["sold_amount"] == 2400.0 and summary["month"]["charged_to_dues"] == 1500.0 and summary["month"]["issued_free_cost"] == 3600.0
    assert summary["stock_value"] == 13 * 900.0


async def test_returnable_items_returns_and_reversals(app_client, admin_user, db_session):
    sid, _ = await _create_and_activate_student(app_client, cnic="71001-0000002-1")
    await _login_admin(app_client)
    laptop = await _item(app_client, name="Laptop", category="IT", unit="pcs", sale_price=None, is_returnable=True, opening_quantity=3, opening_unit_cost=80000)
    out = (await app_client.post(f"{I}/issue", json={"item_id": laptop["id"], "quantity": 2, "party_type": "STUDENT", "party_id": sid, "reason": "Project work"})).json()
    held = (await app_client.get(f"{I}/holdings")).json()
    assert [(h["id"], h["still_out"]) for h in held] == [(out["id"], 2.0)]

    back = await app_client.post(f"{I}/movements/{out['id']}/return", json={"quantity": 1, "reason": "Good condition"})
    assert back.status_code == 201 and back.json()["balance_after"] == 2.0
    assert (await app_client.post(f"{I}/movements/{out['id']}/return", json={"quantity": 5})).status_code == 422
    assert (await app_client.post(f"{I}/movements/{out['id']}/reverse", json={"reason": "Wrong student"})).status_code == 422   # a return exists
    assert (await app_client.post(f"{I}/movements/{back.json()['id']}/reverse", json={"reason": "Entered twice"})).status_code == 201
    assert (await app_client.post(f"{I}/movements/{back.json()['id']}/reverse", json={"reason": "Entered twice"})).status_code == 422  # only once
    rev = await app_client.post(f"{I}/movements/{out['id']}/reverse", json={"reason": "Wrong student"})
    assert rev.status_code == 201
    assert (await app_client.get(f"{I}/holdings")).json() == []
    item = next(i for i in (await app_client.get(f"{I}/items")).json() if i["id"] == laptop["id"])
    assert item["quantity_on_hand"] == 3.0

    # correction after a count, with a reason; never below zero
    assert (await app_client.post(f"{I}/items/{laptop['id']}/adjust", json={"change": -1, "reason": "Screen broken, written off"})).status_code == 201
    assert (await app_client.post(f"{I}/items/{laptop['id']}/adjust", json={"change": -10, "reason": "Count difference"})).status_code == 422
    history = (await app_client.get(f"{I}/movements", params={"item_id": laptop["id"]})).json()
    assert [h["kind"] for h in history] == ["ADJUSTED", "REVERSED", "REVERSED", "RETURNED", "ISSUED", "OPENING"]
    logged = (await db_session.execute(select(AuditLog.action).where(AuditLog.action.like("inventory.%")))).scalars().all()
    assert {"inventory.item_created", "inventory.opening", "inventory.issued", "inventory.returned", "inventory.reversed", "inventory.adjusted"} <= set(logged)


async def test_reversing_a_sale_voids_its_income_and_dues_are_waived(app_client, admin_user, db_session):
    sid, _ = await _create_and_activate_student(app_client, cnic="71001-0000003-1")
    await _login_admin(app_client)
    tie = await _item(app_client, name="College tie", category="Uniform", unit="pcs", sale_price=500, opening_quantity=10, opening_unit_cost=300)
    sale = (await app_client.post(f"{I}/issue", json={"item_id": tie["id"], "quantity": 1, "party_type": "STUDENT", "party_id": sid, "charge_mode": "PAID_NOW"})).json()
    dues = (await app_client.post(f"{I}/issue", json={"item_id": tie["id"], "quantity": 2, "party_type": "STUDENT", "party_id": sid, "charge_mode": "ADD_TO_DUES"})).json()
    assert (await app_client.post(f"{I}/movements/{sale['id']}/reverse", json={"reason": "Sold by mistake"})).status_code == 201
    assert (await app_client.post(f"{I}/movements/{dues['id']}/reverse", json={"reason": "Charged by mistake"})).status_code == 201
    book = (await app_client.get("/api/v1/accounts/book", params={"kind": "INCOME"})).json()
    assert not any(b["category"] == "Store sales" for b in book)
    fines = (await app_client.get("/api/v1/fines", params={"source": "INVENTORY", "status": "WAIVED"})).json()
    assert [f["amount"] for f in fines] == [1000.0]


async def test_who_can_do_what(app_client, admin_user, db_session):
    team = await _team(app_client, db_session)
    await _login_admin(app_client)
    pen = await _item(app_client, name="Marker", unit="pcs", opening_quantity=10, opening_unit_cost=50)
    await _as(app_client, team, "officer")        # accounts office: runs the store, but corrections are for the administration
    assert (await app_client.get(f"{I}/items")).status_code == 200
    assert (await app_client.post(f"{I}/issue", json={"item_id": pen["id"], "quantity": 1, "party_type": "DEPARTMENT", "department": "Exams"})).status_code == 201
    assert (await app_client.post(f"{I}/items/{pen['id']}/adjust", json={"change": -1, "reason": "Lost in transit"})).status_code == 403
    await _as(app_client, team, "t1")             # a teacher: no access
    assert (await app_client.get(f"{I}/items")).status_code == 403
    assert (await app_client.get("/dashboard/admin/inventory")).status_code == 200   # the page itself checks roles in the browser