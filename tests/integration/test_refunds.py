"""Fee refunds: request (accounts) -> approve (a different administrator) -> pay out (voucher). Refunds never exceed
what was paid, withdrawals write charges off first, and balances, the student's page and the finance report follow."""
from __future__ import annotations

import pytest

from app.core.config import settings
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_fees import _create_active_fee_structure, _create_and_activate_student, _login, _login_admin

pytestmark = pytest.mark.asyncio
F = "/api/v1/fees/refunds"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)


async def _setup(client, db, cnic, total=10000, n=2, paid=10000):
    sid, user = await _create_and_activate_student(client, cnic=cnic)
    await _create_active_fee_structure(client, total_amount=total, installment_count=n)
    await _login_admin(client)
    aid = next(a for a in (await client.get("/api/v1/fees/assignments")).json() if a["payer_id"] == sid)["id"]
    if paid:
        assert (await client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": paid, "method": "CASH"})).status_code == 201
    acc_user, first = await _staff(client, db, f"ACC{cnic[3:6]}", "ACCOUNTS_OFFICER", f"35202-{cnic[6:13]}-1")
    acc = (acc_user, await _first_login(client, acc_user, first))
    return sid, user, aid, acc


async def test_withdrawal_refund_full_cycle(app_client, admin_user, db_session):
    sid, user, aid, acc = await _setup(app_client, db_session, "78001-0000001-1")
    await _login(app_client, *acc)
    raw = await app_client.get(f"{F}/position/{aid}")
    assert raw.status_code == 200, raw.text
    pos = raw.json()
    assert pos["credit_balance"] == 0 and pos["net_paid"] == 10000 and pos["max_write_off"] == 10000
    too_much = await app_client.post(F, json={"fee_assignment_id": aid, "kind": "WITHDRAWAL", "amount": 6000, "write_off": 5000, "reason": "Left after two weeks"})
    assert too_much.status_code == 422 and "at most Rs. 5,000.00 can be refunded" in too_much.text
    r = await app_client.post(F, json={"fee_assignment_id": aid, "kind": "WITHDRAWAL", "amount": 5000, "write_off": 5000, "reason": "Left after two weeks"})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    dup = await app_client.post(F, json={"fee_assignment_id": aid, "kind": "OTHER", "amount": 1, "reason": "Second request same fee"})
    assert dup.status_code == 422 and "in progress" in dup.text
    # the accounts officer cannot approve
    assert (await app_client.post(f"{F}/{rid}/decision", json={"approve": True})).status_code == 403
    assert (await app_client.post(f"{F}/{rid}/pay", json={"method": "CASH"})).status_code == 422          # not approved yet

    await _login_admin(app_client)
    ok = await app_client.post(f"{F}/{rid}/decision", json={"approve": True, "note": "As per refund policy"})
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
    assert (await app_client.get(f"{F}/position/{aid}")).json()["credit_balance"] == 5000                   # write-off made the credit

    await _login(app_client, *acc)
    no_ref = await app_client.post(f"{F}/{rid}/pay", json={"method": "CHEQUE"})
    assert no_ref.status_code == 422
    paid = (await app_client.post(f"{F}/{rid}/pay", json={"method": "CHEQUE", "reference": "CHQ-445566"})).json()
    assert paid["status"] == "PAID" and paid["voucher_number"].startswith("RFND-")
    pos = (await app_client.get(f"{F}/position/{aid}")).json()
    assert pos["credit_balance"] == 0 and pos["net_paid"] == 5000 and pos["outstanding"] == 0
    pdf = await app_client.get(f"/api/v1/fees/receipts/{paid['ledger_entry_id']}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"

    # the student sees the refund as a negative line with its voucher, and owes nothing
    await _login(app_client, user, "StudentPass123!")
    acct = (await app_client.get("/api/v1/fees/accounts/me")).json()
    line = next(p for p in acct["payments"] if p["amount"] < 0)
    assert line["amount"] == -5000 and line["receipt_number"] == paid["voucher_number"]
    assert acct["summary"]["remaining"] == 0


async def test_overpayment_refund_and_requester_cannot_approve_own(app_client, admin_user, db_session):
    sid, _user, aid, acc = await _setup(app_client, db_session, "78001-0000002-2", total=10000, n=2, paid=10000)
    await _login_admin(app_client)
    # a scholarship after payment cannot add credit (it only reduces unpaid instalments), so there is nothing to refund
    nothing = await app_client.post(F, json={"fee_assignment_id": aid, "kind": "OVERPAYMENT", "amount": 100, "reason": "Scholarship after payment"})
    assert nothing.status_code == 422
    wo = await app_client.post(F, json={"fee_assignment_id": aid, "kind": "OVERPAYMENT", "amount": 100, "write_off": 100, "reason": "Overpayment with write-off"})
    assert wo.status_code == 422 and "Withdrawal" in wo.text
    own = (await app_client.post(F, json={"fee_assignment_id": aid, "kind": "WITHDRAWAL", "amount": 2000, "write_off": 2000, "reason": "Admin raised it"})).json()
    selfie = await app_client.post(f"{F}/{own['id']}/decision", json={"approve": True})
    assert selfie.status_code == 403 and "cannot approve" in selfie.text
    # another administrator rejects it, with a reason
    from tests.integration.test_exam_controller import _exam_user

    user2, pw2 = await _exam_user(app_client, db_session)
    await _login_admin(app_client)
    emp = (await app_client.get("/api/v1/employees", params={"q": user2})).json()["items"][0]["id"]
    assert (await app_client.put(f"/api/v1/employees/{emp}/access-roles", json={"extra_roles": ["ADMIN"]})).status_code == 200
    await _login(app_client, user2, pw2)
    assert (await app_client.post(f"{F}/{own['id']}/decision", json={"approve": False})).status_code == 422     # reason needed
    rej = await app_client.post(f"{F}/{own['id']}/decision", json={"approve": False, "note": "Not covered by policy"})
    assert rej.json()["status"] == "REJECTED"
    assert (await app_client.get(f"{F}/position/{aid}")).json()["credit_balance"] == 0                       # nothing changed


async def test_cannot_refund_more_than_was_paid(app_client, admin_user, db_session):
    _sid, _user, aid, acc = await _setup(app_client, db_session, "78001-0000003-3", total=10000, n=2, paid=3000)
    await _login(app_client, *acc)
    r = await app_client.post(F, json={"fee_assignment_id": aid, "kind": "WITHDRAWAL", "amount": 5000, "write_off": 7000, "reason": "Left in week one"})
    assert r.status_code == 422 and "has been paid" in r.text
    # paid 3,000 of 10,000: writing off 7,000 leaves 3,000 owed, which the payment covers, so nothing is refundable
    none = await app_client.post(F, json={"fee_assignment_id": aid, "kind": "WITHDRAWAL", "amount": 3000, "write_off": 7000, "reason": "Left in week one"})
    assert none.status_code == 422 and "at most Rs. 0.00" in none.text and "write off Rs. 10,000.00" in none.text
    ok = await app_client.post(F, json={"fee_assignment_id": aid, "kind": "WITHDRAWAL", "amount": 3000, "write_off": 10000, "reason": "Left in week one"})
    assert ok.status_code == 201


async def test_partly_paid_withdrawal_needs_enough_write_off(app_client, admin_user, db_session):
    # the case the browser walkthrough caught: paid 6,000 of 12,000, wants 1,000 back
    _sid, _user, aid, acc = await _setup(app_client, db_session, "78001-0000006-6", total=12000, n=4, paid=6000)
    await _login(app_client, *acc)
    bad = await app_client.post(F, json={"fee_assignment_id": aid, "kind": "WITHDRAWAL", "amount": 1000, "write_off": 1000, "reason": "Left after week one"})
    assert bad.status_code == 422 and "write off Rs. 7,000.00" in bad.text
    rid = (await app_client.post(F, json={"fee_assignment_id": aid, "kind": "WITHDRAWAL", "amount": 1000, "write_off": 7000,
                                           "reason": "Left after week one"})).json()["id"]
    await _login_admin(app_client)
    assert (await app_client.post(f"{F}/{rid}/decision", json={"approve": True})).status_code == 200
    await _login(app_client, *acc)
    paid = await app_client.post(f"{F}/{rid}/pay", json={"method": "CASH"})
    assert paid.status_code == 200, paid.text
    pos = (await app_client.get(f"{F}/position/{aid}")).json()
    assert pos["outstanding"] == 0 and pos["credit_balance"] == 0 and pos["net_paid"] == 5000


async def test_finance_report_subtracts_refunds(app_client, admin_user, db_session):
    _sid, _user, aid, acc = await _setup(app_client, db_session, "78001-0000004-4", total=8000, n=1, paid=8000)
    before = (await app_client.get("/api/v1/accounts/summary")).json()
    await _login(app_client, *acc)
    rid = (await app_client.post(F, json={"fee_assignment_id": aid, "kind": "WITHDRAWAL", "amount": 3000, "write_off": 3000, "reason": "Left mid term"})).json()["id"]
    await _login_admin(app_client)
    await app_client.post(f"{F}/{rid}/decision", json={"approve": True})
    await _login(app_client, *acc)
    await app_client.post(f"{F}/{rid}/pay", json={"method": "CASH"})
    await _login_admin(app_client)
    after = (await app_client.get("/api/v1/accounts/summary")).json()
    key = next(k for k in before if "fee" in k.lower())
    assert round(before[key] - after[key], 2) == 3000


async def test_refund_pages_and_students_are_kept_out(app_client, admin_user, db_session):
    _sid, user, aid, acc = await _setup(app_client, db_session, "78001-0000005-5", paid=0)
    for path in ("/dashboard/admin/refunds", "/dashboard/accounts/refunds"):
        page = await app_client.get(path)
        assert page.status_code == 200 and "refunds.js" in page.text and 'data-allowed-roles="ADMIN,ACCOUNTS_OFFICER"' in page.text
    await _login(app_client, user, "StudentPass123!")
    assert (await app_client.get(F)).status_code == 403
    assert (await app_client.get(f"{F}/position/{aid}")).status_code == 403