"""Owner decisions 4-7: late fee grace, cap and pause; leaving cancels what is not yet due; full clearance for a
degree; money that arrives above what is owed is held as credit and can pay another fee; cancelled write-offs."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.clock import institute_today
from app.core.config import settings
from app.fees.assignment_service import FeeStatusCalculator
from app.fees.models import FeeLedgerEntry, LedgerEntryType
from tests.integration.test_fees import _create_active_fee_structure, _create_and_activate_student, _login_admin

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)


def _e(kind, amount, **kw):
    kw.setdefault("created_at", datetime(2026, 1, 1, tzinfo=timezone.utc))
    return FeeLedgerEntry(id=uuid.uuid4(), fee_assignment_id=uuid.uuid4(), entry_type=kind, amount=amount, late_fee_charged_amount=0, **kw)


# ====================================================================== calculator (no database)
async def test_grace_days_cap_and_pause(monkeypatch):
    today = date(2026, 3, 31)
    debit = _e(LedgerEntryType.DEBIT, 10000, due_date=date(2026, 3, 1), installment_number=1)
    monkeypatch.setattr(settings, "fee_late_fee_cap", 0.0)
    monkeypatch.setattr(settings, "fee_late_fee_grace_days", 0)
    assert FeeStatusCalculator.installments([debit], 100, as_of=today)[0]["late_fee"] == 3000.0            # 30 days
    monkeypatch.setattr(settings, "fee_late_fee_grace_days", 5)
    assert FeeStatusCalculator.installments([debit], 100, as_of=today)[0]["late_fee"] == 2500.0            # from the 6th
    monkeypatch.setattr(settings, "fee_late_fee_cap", 2000.0)
    assert FeeStatusCalculator.installments([debit], 100, as_of=today)[0]["late_fee"] == 2000.0            # capped
    monkeypatch.setattr(settings, "fee_late_fee_cap", 0.0)
    paused = (date(2026, 3, 11), date(2026, 3, 21))                                                         # 10 days suspended
    assert FeeStatusCalculator.installments([debit], 100, as_of=today, pause=paused)[0]["late_fee"] == 1500.0
    still = (date(2026, 3, 21), None)                                                                       # left on the 21st
    assert FeeStatusCalculator.installments([debit], 100, as_of=today, pause=still)[0]["late_fee"] == 1500.0
    assert FeeStatusCalculator.installments([debit], 100, as_of=date(2026, 6, 30), pause=still)[0]["late_fee"] == 1500.0   # stays frozen


async def test_cancelled_write_off_restores_the_installment_without_a_new_one():
    d1 = _e(LedgerEntryType.DEBIT, 10000, due_date=date(2026, 3, 1), installment_number=1)
    w = _e(LedgerEntryType.CONCESSION, 4000, related_entry_id=d1.id, installment_number=1, description="Withdrawal write-off (refund x)")
    undo = _e(LedgerEntryType.DEBIT, 4000, related_entry_id=w.id, installment_number=1, due_date=d1.due_date, description="Write-off cancelled (refund x)")
    rows = FeeStatusCalculator.installments([d1, w, undo], None, as_of=date(2026, 2, 1))
    assert len(rows) == 1 and rows[0]["concession"] == 0.0 and rows[0]["remaining"] == 10000.0


async def test_credit_moves_between_fees_in_the_calculator():
    d = _e(LedgerEntryType.DEBIT, 5000, due_date=date(2026, 3, 1), installment_number=1)
    paid = _e(LedgerEntryType.CREDIT, 7000, paid_on=date(2026, 2, 1))
    s = FeeStatusCalculator.compute([d, paid], None, as_of=date(2026, 2, 2))
    assert s["credit"] == 2000.0 and s["total_remaining"] == 0.0
    out = _e(LedgerEntryType.CREDIT_OUT, 2000, paid_on=date(2026, 2, 3))
    s = FeeStatusCalculator.compute([d, paid, out], None, as_of=date(2026, 2, 3))
    assert s["credit"] == 0.0 and s["total_remaining"] == 0.0 and s["total_paid"] == 5000.0
    d2 = _e(LedgerEntryType.DEBIT, 3000, due_date=date(2026, 3, 1), installment_number=1)
    s2 = FeeStatusCalculator.compute([d2, _e(LedgerEntryType.CREDIT_IN, 2000, paid_on=date(2026, 2, 3))], None, as_of=date(2026, 2, 3))
    assert s2["total_remaining"] == 1000.0 and s2["total_paid"] == 2000.0


# ====================================================================== through the API
async def _fee(client, n=6, total=60000):
    sid, _ = await _create_and_activate_student(client, cnic="75001-0000001-1")
    await _create_active_fee_structure(client, total_amount=total, installment_count=n)
    await _login_admin(client)
    a = (await client.get("/api/v1/fees/assignments")).json()[0]
    return sid, a["id"]


async def test_leaving_cancels_what_is_not_yet_due_and_pauses_late_fees(app_client, admin_user, db_session):
    sid, aid = await _fee(app_client)
    acct = (await app_client.get(f"/api/v1/fees/accounts/students/{sid}")).json()
    rows = acct["assignments"][0]["installments"]
    today = institute_today()
    future = sum(r["remaining"] for r in rows if r["due_date"] and date.fromisoformat(r["due_date"]) > today)
    due = sum(r["remaining"] for r in rows if r["due_date"] and date.fromisoformat(r["due_date"]) <= today)
    assert future > 0 and due > 0
    r = await app_client.post(f"/api/v1/students/{sid}/status", json={"new_status": "DROPPED", "reason": "Left for another city"})
    assert r.status_code == 200, r.text
    acct = (await app_client.get(f"/api/v1/fees/accounts/students/{sid}")).json()
    f = acct["assignments"][0]
    assert f["remaining"] == due and f["late_fee_paused_from"] == str(today)
    assert any(c["name"].startswith("Cancelled: not yet due") for c in f["concessions"])


async def test_office_can_keep_future_fees_and_suspension_only_pauses(app_client, admin_user):
    sid, aid = await _fee(app_client)
    before = (await app_client.get(f"/api/v1/fees/accounts/students/{sid}")).json()["summary"]["remaining"]
    assert (await app_client.post(f"/api/v1/students/{sid}/status", json={"new_status": "SUSPENDED"})).status_code == 200
    f = (await app_client.get(f"/api/v1/fees/accounts/students/{sid}")).json()["assignments"][0]
    assert f["remaining"] == before and f["late_fee_paused_from"] and not f["late_fee_paused_until"]
    assert (await app_client.post(f"/api/v1/students/{sid}/status", json={"new_status": "ACTIVE"})).status_code == 200
    f = (await app_client.get(f"/api/v1/fees/accounts/students/{sid}")).json()["assignments"][0]
    assert f["late_fee_paused_until"] == str(institute_today())
    assert (await app_client.post(f"/api/v1/students/{sid}/status", json={"new_status": "DROPPED", "keep_future_fees": True})).status_code == 200
    assert (await app_client.get(f"/api/v1/fees/accounts/students/{sid}")).json()["summary"]["remaining"] == before


async def test_degree_and_completion_need_every_fee_paid(app_client, admin_user):
    sid, aid = await _fee(app_client)
    r = await app_client.post(f"/api/v1/students/{sid}/status", json={"new_status": "GRADUATED"})
    assert r.status_code == 422 and "not yet due" in r.text
    clear = (await app_client.get(f"/api/v1/records/students/{sid}/clearance")).json()
    assert any("not yet due" in p for p in clear["problems"])
    ok = await app_client.post(f"/api/v1/students/{sid}/status", json={"new_status": "GRADUATED", "reason": "Board decision 14/2026: dues waived"})
    assert ok.status_code == 200


async def test_bank_money_above_what_is_owed_becomes_credit_and_pays_another_fee(app_client, admin_user, db_session):
    from app.fees.assignment_service import ChallanService
    from app.fees.payment_models import PaymentChallan

    sid, aid = await _fee(app_client, n=1, total=10000)
    acct = (await app_client.get(f"/api/v1/fees/accounts/students/{sid}")).json()
    ch = acct["assignments"][0]["installments"][0]["challan"]
    assert ch and ch["status"] == "PENDING"
    pay = await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 10000, "method": "CASH"})   # paid at the counter too
    assert pay.status_code == 201
    challan = await db_session.get(PaymentChallan, uuid.UUID(ch["id"]))
    await ChallanService(db_session).confirm_from_webhook(consumer_number=challan.consumer_number, gateway_reference="1LINK-777")
    await db_session.commit()
    again = await ChallanService(db_session).confirm_from_webhook(consumer_number=challan.consumer_number, gateway_reference="1LINK-777")
    assert again.status.value == "PAID"                                                                     # duplicate callback: no second payment
    acct = (await app_client.get(f"/api/v1/fees/accounts/students/{sid}")).json()
    f = acct["assignments"][0]
    assert f["credit"] == 10000.0 and acct["summary"]["credit_balance"] == 10000.0
    # A second fee for the same student is paid from the credit.
    await app_client.post("/api/v1/fees/types", json={"code": "LAB", "name": "Lab fee"})
    t = next(x for x in (await app_client.get("/api/v1/fees/types")).json() if x["code"] == "LAB")
    st = (await app_client.post("/api/v1/fees/structures", json={"fee_type_id": t["id"], "name": "Lab — Fall 2026", "term_label": "Fall 2026",
                                                                  "total_amount": 4000, "installment_count": 1, "effective_from": "2026-09-01"})).json()
    assert (await app_client.post(f"/api/v1/fees/structures/{st['id']}/activate")).status_code == 200
    acct = (await app_client.get(f"/api/v1/fees/accounts/students/{sid}")).json()
    lab = next(x for x in acct["assignments"] if x["fee_name"].startswith("Lab"))
    mv = await app_client.post("/api/v1/fees/accounts/credit/move", json={"from_assignment_id": aid, "to_assignment_id": lab["id"]})
    assert mv.status_code == 200, mv.text
    assert mv.json() == {"moved": 4000.0, "credit_left": 6000.0, "still_owed": 0.0}
    acct = (await app_client.get(f"/api/v1/fees/accounts/students/{sid}")).json()
    assert acct["summary"]["credit_balance"] == 6000.0 and acct["summary"]["remaining"] == 0.0
    # The cash book counts the money once: 10,000 at the counter + 10,000 from the bank, nothing for the move.
    today = str(institute_today())
    book = (await app_client.get("/api/v1/report-center/run/accounts.cash_book", params={"from_date": today, "to_date": today})).json()
    assert book["totals"]["received"] == 10000.0                                                            # cash only (the bank payment is not cash)
    too_much = await app_client.post("/api/v1/fees/accounts/credit/move", json={"from_assignment_id": aid, "to_assignment_id": lab["id"]})
    assert too_much.status_code == 422
