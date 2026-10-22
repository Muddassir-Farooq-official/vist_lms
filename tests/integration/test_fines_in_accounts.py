"""Collected fines are income in Accounts (with how they were paid and a receipt); fee payments appear in the day book;
the staff fee statement shows which installments each payment settled."""
from __future__ import annotations

import pytest

from app.core.config import settings
from tests.integration.test_fees import _create_active_fee_structure, _create_and_activate_student, _login_admin

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)


async def _fine(client, sid, amount, source="DISCIPLINARY"):
    r = await client.post("/api/v1/fines", json={"payer_type": "STUDENT", "payer_id": sid, "amount": amount, "reason": "Test fine", "source": source})
    assert r.status_code == 201, r.text
    return r.json()


async def test_paid_fines_show_in_accounts_with_receipt(app_client, admin_user):
    sid, _ = await _create_and_activate_student(app_client, cnic="72001-0000001-1")
    await _login_admin(app_client)
    f1, f2, f3 = await _fine(app_client, sid, 500), await _fine(app_client, sid, 300, "LIBRARY"), await _fine(app_client, sid, 200)

    no_ref = await app_client.post(f"/api/v1/fines/{f1['id']}/mark-paid", json={"method": "CHEQUE"})
    assert no_ref.status_code == 422
    paid = await app_client.post(f"/api/v1/fines/{f1['id']}/mark-paid", json={"method": "BANK_TRANSFER", "reference": "HBL-77"})
    assert paid.status_code == 200, paid.text
    p = paid.json()
    assert p["status"] == "PAID" and p["payment_method"] == "BANK_TRANSFER" and p["receipt_number"].startswith("FINE-")
    pdf = await app_client.get(f"/api/v1/fines/{f1['id']}/receipt.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert (await app_client.post(f"/api/v1/fines/{f2['id']}/mark-paid", json={"note": "cash at counter"})).json()["payment_method"] == "CASH"
    assert (await app_client.post(f"/api/v1/fines/{f3['id']}/waive", json={"note": "first offence"})).status_code == 200

    s = (await app_client.get("/api/v1/accounts/summary")).json()
    assert s["fines_collected"] == 800.0 and s["fines_waived"] == 200.0 and s["fines_outstanding"]["count"] == 0
    assert {r["source"]: r["amount"] for r in s["fines_by_source"]} == {"DISCIPLINARY": 500.0, "LIBRARY": 300.0}
    assert s["total_income"] == round(s["fees_collected"] + 800.0 + s["other_income"], 2)
    assert sum(m["fines"] for m in s["monthly"]) == 800.0
    book = (await app_client.get("/api/v1/accounts/book", params={"kind": "INCOME"})).json()
    fine_rows = [b for b in book if b["category"].startswith("Fines")]
    assert sorted(b["amount"] for b in fine_rows) == [300.0, 500.0] and any(b["reference"] == p["receipt_number"] for b in fine_rows)


async def test_fee_payment_settles_installments_and_reaches_the_book(app_client, admin_user):
    sid, _ = await _create_and_activate_student(app_client, cnic="72001-0000002-1")
    await _create_active_fee_structure(app_client, total_amount=9000, installment_count=3)
    await _login_admin(app_client)
    aid = next(a for a in (await app_client.get("/api/v1/fees/assignments")).json() if a["payer_id"] == sid)["id"]
    r = await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 4500, "method": "CASH"})
    assert r.status_code == 201, r.text

    st = (await app_client.get(f"/api/v1/fees/assignments/{aid}/statement")).json()
    assert [(i["label"], i["paid"], i["remaining"], i["status"]) for i in st["installments"]][:2] == [
        ("Installment 1", 3000.0, 0.0, "PAID"), ("Installment 2", 1500.0, 1500.0, st["installments"][1]["status"])]
    assert st["installments"][1]["status"] in ("PARTIAL", "OVERDUE")
    pay = next(line for line in st["lines"] if line["what"] == "Payment")
    assert pay["applied_to"] == [{"installment": "Installment 1", "amount": 3000.0}, {"installment": "Installment 2", "amount": 1500.0}]
    assert st["balance"] == 4500.0 and st["lines"][-1]["balance"] == 4500.0

    book = (await app_client.get("/api/v1/accounts/book", params={"kind": "INCOME"})).json()
    fee = [b for b in book if b["category"] == "Student / staff fees"]
    assert [b["amount"] for b in fee] == [4500.0] and fee[0]["reference"].startswith("RCPT-") and fee[0]["party"]