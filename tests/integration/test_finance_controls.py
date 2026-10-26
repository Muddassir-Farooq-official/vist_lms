"""Finance controls: closing the books, late fee cap and standing, one reference per payment, cancelling an approved
refund, paying back a security deposit, price re-approval and unique invoices, undoing a mistaken activation,
maker-checker on large expenses, and the batched fee list."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.clock import institute_today
from app.core.config import settings
from tests.integration.test_accounts import _cat as _acat
from tests.integration.test_accounts import _spend
from tests.integration.test_fees import _create_active_fee_structure, _create_and_activate_student, _login_admin
from tests.integration.test_launch_step3_fees import _student_with_fee
from tests.integration.test_payroll import PR, TODAY, _as, _prepare, _team
from tests.integration.test_purchasing import P, _journey_to
from tests.integration.test_purchasing import _team as _pteam
from tests.integration.test_refunds import F as RF
from tests.integration.test_refunds import _setup as _refund_setup

pytestmark = pytest.mark.asyncio
FA = "/api/v1/fees/assignments"
A = "/api/v1/accounts"

async def _approve(client, run_id):
    """Payroll now goes DRAFT -> SUBMITTED (accounts office) -> APPROVED (administration). Submit, then approve."""
    sub = await client.post(f"/api/v1/payroll/runs/{run_id}/submit")
    if sub.status_code != 200 and "Only a draft" not in sub.text:     # already submitted is fine; a real problem is returned
        return sub
    return await client.post(f"/api/v1/payroll/runs/{run_id}/approve")



@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def test_closed_books_refuse_back_dated_entries_and_reopening_needs_a_reason(app_client, admin_user):
    _sid, _user, aid = await _student_with_fee(app_client, "75001-0000001-1", total=10000, n=2)
    closed = institute_today() - timedelta(days=3)
    r = await app_client.put(f"{A}/period-close", json={"closed_through": str(closed)})
    assert r.status_code == 200 and r.json()["closed_through"] == str(closed)
    assert (await app_client.put(f"{A}/period-close", json={"closed_through": str(institute_today())})).status_code == 422     # only ended periods
    old = await app_client.post(f"{FA}/{aid}/payments", json={"amount": 100, "method": "CASH", "paid_on": str(closed - timedelta(days=1))})
    assert old.status_code == 422 and "books are closed" in old.text
    assert (await app_client.post(f"{FA}/{aid}/payments", json={"amount": 100, "method": "CASH"})).status_code == 201
    cat = await _acat(app_client, "EXPENSE", "Utilities")
    late_exp = await _spend(app_client, cat["id"], amount="500", expense_date=str(closed))
    assert late_exp.status_code == 422 and "books are closed" in late_exp.text
    assert (await app_client.put(f"{A}/period-close", json={"closed_through": None})).status_code == 422                     # reopening needs a reason
    ok = await app_client.put(f"{A}/period-close", json={"closed_through": None, "reason": "Bank statement correction for September"})
    assert ok.status_code == 200 and ok.json()["closed_through"] is None


async def test_late_fee_is_capped_and_a_paid_instalment_does_not_keep_arrears(app_client, admin_user, monkeypatch, db_session):
    from app.fees.assignment_service import FeeStatusCalculator

    monkeypatch.setattr(settings, "fee_late_fee_cap", 500.0)

    class E:  # a minimal ledger row
        def __init__(self, **kw):
            self.__dict__.update(dict(related_entry_id=None, late_fee_charged_amount=0, created_at=None, paid_on=None, installment_number=1, description=None), **kw)

    import uuid as _uuid
    from datetime import datetime, timezone

    from app.fees.models import LedgerEntryType

    today = institute_today()
    debit = E(id=_uuid.uuid4(), entry_type=LedgerEntryType.DEBIT, amount=5000, due_date=today - timedelta(days=40),
              created_at=datetime.now(timezone.utc) - timedelta(days=60))
    unpaid = FeeStatusCalculator.compute([debit], 200, as_of=today)
    assert unpaid["accrued_fine"] == 500.0 and unpaid["total_due_now"] == 5500.0          # capped, not 40 x 200
    paid = E(id=_uuid.uuid4(), entry_type=LedgerEntryType.CREDIT, amount=5000, due_date=None, paid_on=today,
             created_at=datetime.now(timezone.utc))
    settled = FeeStatusCalculator.compute([debit, paid], 200, as_of=today)
    assert settled["accrued_fine"] == 500.0 and settled["total_due_now"] == 0             # still postable, but not "in arrears"


async def test_the_same_bank_reference_cannot_pay_twice(app_client, admin_user):
    s1, _u1 = await _create_and_activate_student(app_client, cnic="75001-0000002-2")
    s2, _u2 = await _create_and_activate_student(app_client, cnic="75001-0000003-3")
    await _create_active_fee_structure(app_client, total_amount=10000, installment_count=2)
    await _login_admin(app_client)
    rows = (await app_client.get(FA)).json()
    a1 = next(a for a in rows if a["payer_id"] == s1)["id"]
    a2 = next(a for a in rows if a["payer_id"] == s2)["id"]
    assert (await app_client.post(f"{FA}/{a1}/payments", json={"amount": 1000, "method": "BANK_DEPOSIT", "reference": "HBL 5566"})).status_code == 201
    dup = await app_client.post(f"{FA}/{a2}/payments", json={"amount": 1000, "method": "BANK_DEPOSIT", "reference": "hbl5566"})
    assert dup.status_code == 422 and "already used" in dup.text
    assert (await app_client.post(f"{FA}/{a2}/payments", json={"amount": 1000, "method": "BANK_DEPOSIT", "reference": "HBL 5566/2"})).status_code == 201
    assert (await app_client.post(f"{FA}/{a2}/payments", json={"amount": 10, "method": "CASH"})).status_code == 201       # cash needs no reference


async def test_an_approved_refund_can_be_cancelled_and_its_write_off_is_put_back(app_client, admin_user, db_session):
    _sid, _user, aid, acc = await _refund_setup(app_client, db_session, "75001-0000004-4")
    from tests.integration.test_refunds import _login

    await _login(app_client, *acc)
    rid = (await app_client.post(RF, json={"fee_assignment_id": aid, "kind": "WITHDRAWAL", "amount": 5000, "write_off": 5000, "reason": "Left after two weeks"})).json()["id"]
    await _login_admin(app_client)
    assert (await app_client.post(f"{RF}/{rid}/decision", json={"approve": True})).status_code == 200
    assert (await app_client.get(f"{RF}/position/{aid}")).json()["credit_balance"] == 5000
    r = await app_client.post(f"{RF}/{rid}/cancel", json={"reason": "Student decided to stay"})
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    pos = (await app_client.get(f"{RF}/position/{aid}")).json()
    assert pos["credit_balance"] == 0 and pos["owed_after_discounts"] == 10000
    assert (await app_client.post(f"{RF}/{rid}/pay", json={"method": "CASH"})).status_code == 422


async def test_a_security_deposit_is_paid_back_once(app_client, admin_user, db_session, monkeypatch):
    monkeypatch.setattr(settings, "payroll_security_percent", 10.0)
    team = await _team(app_client, db_session)
    await _as(app_client, team, "officer")
    run = await _prepare(app_client)
    await _login_admin(app_client)
    await _approve(app_client, run['id'])
    await _as(app_client, team, "officer")
    await app_client.post(f"{PR}/runs/{run['id']}/pay", json={"payment_method": "CASH", "paid_date": str(TODAY)})
    sec = (await app_client.get(f"{PR}/security")).json()
    b = next(p for p in sec["people"] if p["employee_id"] == team["b"]["id"])
    assert b["held"] == 3000.0
    r = await app_client.post(f"{PR}/security/{team['b']['id']}/release", json={"payment_method": "CASH", "paid_date": str(TODAY)})
    assert r.status_code == 200 and r.json()["paid_back"] == 3000.0 and r.json()["expense_reference"].startswith("EXP-")
    again = await app_client.post(f"{PR}/security/{team['b']['id']}/release", json={"payment_method": "CASH", "paid_date": str(TODAY)})
    assert again.status_code == 422 and "Nothing is held" in again.text


async def test_a_purchase_well_over_estimate_waits_for_the_price_and_invoices_are_unique(app_client, admin_user, db_session):
    team = await _pteam(app_client, db_session)
    po, vendor = await _journey_to(app_client, team, "ordered")
    items = (await app_client.get(f"{P}/{po['id']}")).json()["items"]
    lines = [{"item_id": items[0]["id"], "received_quantity": 3, "actual_unit_price": 6000}, {"item_id": items[1]["id"], "received_quantity": 1, "actual_unit_price": 12000}]
    got = await app_client.post(f"{P}/{po['id']}/receive", json={"invoice_no": "INV-900", "lines": lines})      # 30,000 vs 22,000 approved
    assert got.status_code == 200 and "pay" not in got.json()["actions"]
    pay = await app_client.post(f"{P}/{po['id']}/pay", data={"payment_method": "CASH", "paid_date": str(institute_today())})
    assert pay.status_code == 422 and "approve the price" in pay.text
    await _login_admin(app_client)
    ok = await app_client.post(f"{P}/{po['id']}/approve-price")
    assert ok.status_code == 200 and "approve_price" not in ok.json()["actions"]

    # a second order from the same vendor with the same invoice number is refused
    from tests.integration.test_purchasing import _as as _pas
    from tests.integration.test_purchasing import _request

    await _pas(app_client, team, "t1")
    po2 = await _request(app_client, title="More chairs for room 14")
    await _login_admin(app_client)
    assert (await app_client.post(f"{P}/{po2['id']}/approve")).status_code == 200
    await _pas(app_client, team, "officer")
    assert (await app_client.post(f"{P}/{po2['id']}/order", json={"vendor_id": vendor, "expected_delivery": str(institute_today() + timedelta(days=5))})).status_code == 200
    items2 = (await app_client.get(f"{P}/{po2['id']}")).json()["items"]
    lines2 = [{"item_id": items2[0]["id"], "received_quantity": 3, "actual_unit_price": 4000}, {"item_id": items2[1]["id"], "received_quantity": 1, "actual_unit_price": 10000}]
    dup = await app_client.post(f"{P}/{po2['id']}/receive", json={"invoice_no": " inv-900 ", "lines": lines2})
    assert dup.status_code == 422 and "already recorded" in dup.text


async def test_a_mistaken_activation_can_be_undone_until_someone_pays(app_client, admin_user):
    sid, _user = await _create_and_activate_student(app_client, cnic="75001-0000005-5")
    structure_id = await _create_active_fee_structure(app_client, total_amount=8000, installment_count=2)
    await _login_admin(app_client)
    assert any(a["payer_id"] == sid for a in (await app_client.get(FA)).json())
    r = await app_client.post(f"/api/v1/fees/structures/{structure_id}/undo-activation", json={"reason": "Wrong amount entered"})
    assert r.status_code == 200 and r.json() == {"removed_charges": 1, "status": "DRAFT"}
    assert not any(a["payer_id"] == sid for a in (await app_client.get(FA)).json())
    assert (await app_client.post(f"/api/v1/fees/structures/{structure_id}/activate")).status_code == 200
    aid = next(a for a in (await app_client.get(FA)).json() if a["payer_id"] == sid)["id"]
    await app_client.post(f"{FA}/{aid}/payments", json={"amount": 100, "method": "CASH"})
    blocked = await app_client.post(f"/api/v1/fees/structures/{structure_id}/undo-activation", json={"reason": "Wrong again"})
    assert blocked.status_code == 422 and "cannot be undone" in blocked.text


async def test_an_administrators_own_large_expense_waits_for_another_administrator(app_client, admin_user, db_session):
    from app.auth.models import Role, User, UserRole
    from app.core.security import hash_password

    second = User(username="admin_two", password_hash=hash_password("AdminTwo#2026x"), is_active=True, must_change_password=False)
    db_session.add(second)
    await db_session.flush()
    role = (await db_session.execute(__import__("sqlalchemy").select(Role).where(Role.code == "ADMIN"))).scalar_one()
    db_session.add(UserRole(user_id=second.id, role_id=role.id))
    await db_session.commit()
    await _login_admin(app_client)
    cat = await _acat(app_client, "EXPENSE", "Utilities")
    big = (await _spend(app_client, cat["id"], amount=str(int(settings.expense_approval_limit) + 1000))).json()
    assert big["status"] == "PENDING_APPROVAL"
    own = await app_client.post(f"{A}/expenses/{big['id']}/approve")
    assert own.status_code == 422 and "another administrator" in own.text
    await app_client.post("/api/v1/auth/login", json={"username": "admin_two", "password": "AdminTwo#2026x"})
    assert (await app_client.post(f"{A}/expenses/{big['id']}/approve")).json()["status"] == "APPROVED"