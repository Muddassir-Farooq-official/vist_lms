"""Changing a fee instalment's due date: one student's, or everyone's on a fee."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.audit.models import AuditLog
from app.core.clock import institute_today
from app.core.config import settings
from app.fees.payment_models import PaymentChallan
from app.notifications.models import Notification
from tests.integration.test_fees import _create_active_fee_structure, _create_and_activate_student, _login_admin

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)


async def _rows(client, sid):
    return (await client.get(f"/api/v1/fees/accounts/students/{sid}")).json()["assignments"][0]["installments"]


async def test_one_instalment_due_date_moves_with_its_challan_and_the_student_is_told(app_client, admin_user, db_session):
    sid, _ = await _create_and_activate_student(app_client, cnic="75101-0000001-1")
    await _create_active_fee_structure(app_client, total_amount=60000, installment_count=6)
    await _login_admin(app_client)
    aid = (await app_client.get("/api/v1/fees/assignments")).json()[0]["id"]
    today = institute_today()
    inst = sorted(await _rows(app_client, sid), key=lambda r: r["installment_number"])[1]
    new = today + timedelta(days=10)
    url = f"/api/v1/fees/assignments/{aid}/installments/{inst['ledger_entry_id']}/due-date"

    assert (await app_client.post(url, json={"due_date": str(today - timedelta(days=1)), "reason": "Back-dated"})).status_code == 422   # never into the past
    assert (await app_client.post(url, json={"due_date": str(new), "reason": "x"})).status_code == 422                                  # a reason is needed
    r = await app_client.post(url, json={"due_date": str(new), "reason": "Extension approved by the principal"})
    assert r.status_code == 200, r.text
    assert r.json()["old_due_date"] == inst["due_date"] and r.json()["new_due_date"] == str(new)

    after = next(x for x in await _rows(app_client, sid) if x["ledger_entry_id"] == inst["ledger_entry_id"])
    assert after["due_date"] == str(new) and after["remaining"] == inst["remaining"] and after["late_fee"] == 0    # amount untouched, no late fee now
    ch = (await db_session.execute(select(PaymentChallan).where(PaymentChallan.fee_ledger_entry_id == inst["ledger_entry_id"]))).scalars().all()
    assert ch and all(c.due_date == new for c in ch if c.status.value == "PENDING")
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "fee.due_date_changed"))).scalars().all()
    assert len(log) == 1 and log[0].before["due_date"] == inst["due_date"] and "principal" in log[0].after["reason"]
    told = (await db_session.execute(select(Notification).where(Notification.title == "Fee due date changed"))).scalars().all()
    assert len(told) == 1 and new.strftime("%d %b %Y") in told[0].message


async def test_a_paid_instalment_cannot_be_moved(app_client, admin_user):
    sid, _ = await _create_and_activate_student(app_client, cnic="75101-0000002-1")
    await _create_active_fee_structure(app_client, total_amount=60000, installment_count=6)
    await _login_admin(app_client)
    aid = (await app_client.get("/api/v1/fees/assignments")).json()[0]["id"]
    assert (await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 10000, "method": "CASH", "description": "Counter"})).status_code == 201
    first = next(r for r in await _rows(app_client, sid) if r["installment_number"] == 1)
    assert first["remaining"] == 0
    r = await app_client.post(f"/api/v1/fees/assignments/{aid}/installments/{first['ledger_entry_id']}/due-date",
                              json={"due_date": str(institute_today() + timedelta(days=5)), "reason": "Testing a paid one"})
    assert r.status_code == 422 and "paid in full" in r.text


async def test_everyone_on_a_fee_at_once(app_client, admin_user):
    s1, _ = await _create_and_activate_student(app_client, cnic="75101-0000003-1")
    s2, _ = await _create_and_activate_student(app_client, cnic="75101-0000004-1")
    structure_id = await _create_active_fee_structure(app_client, total_amount=60000, installment_count=6)
    new = institute_today() + timedelta(days=20)
    assert (await app_client.post(f"/api/v1/fees/structures/{structure_id}/installments/9/due-date",
                                  json={"due_date": str(new), "reason": "Wrong instalment"})).status_code == 422
    r = await app_client.post(f"/api/v1/fees/structures/{structure_id}/installments/3/due-date", json={"due_date": str(new), "reason": "Exams moved"})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == 2
    for sid in (s1, s2):
        assert next(x for x in await _rows(app_client, sid) if x["installment_number"] == 3)["due_date"] == str(new)
    again = (await app_client.post(f"/api/v1/fees/structures/{structure_id}/installments/3/due-date", json={"due_date": str(new), "reason": "Exams moved"})).json()
    assert again["changed"] == 0 and again["already_on_that_date"] == 2


async def test_the_daily_late_fee_of_an_active_fee_can_be_set_and_it_doubles_by_the_rule(app_client, admin_user, db_session, monkeypatch):
    sid, _ = await _create_and_activate_student(app_client, cnic="75101-0000005-1")
    structure_id = await _create_active_fee_structure(app_client, total_amount=60000, installment_count=6)
    assert (await app_client.post(f"/api/v1/fees/structures/{structure_id}/late-fee", json={"late_fee_per_day": 50, "reason": "x"})).status_code == 422
    r = await app_client.post(f"/api/v1/fees/structures/{structure_id}/late-fee", json={"late_fee_per_day": 50, "reason": "Board decision"})
    assert r.status_code == 200 and r.json()["late_fee_per_day"] == 50
    monkeypatch.setattr(settings, "fee_late_fee_grace_days", 0)
    monkeypatch.setattr(settings, "fee_late_fee_cap", 0)
    monkeypatch.setattr(settings, "fee_backdated_grace_days", 0)
    monkeypatch.setattr(settings, "fee_late_fee_double_every_days", 7)
    # the fee was only created today; a fee never fines days before it existed, so date its instalments back to the start of term
    from datetime import datetime, timezone

    from sqlalchemy import update

    from app.fees.models import FeeLedgerEntry, LedgerEntryType

    await db_session.execute(update(FeeLedgerEntry).where(FeeLedgerEntry.entry_type == LedgerEntryType.DEBIT)
                             .values(created_at=datetime(2026, 8, 1, tzinfo=timezone.utc)))
    await db_session.commit()
    late = [x for x in await _rows(app_client, sid) if x["late_fee"] > 0]
    assert late, "an instalment due in the past has a late fee"
    for x in late:
        days_late = (institute_today() - date.fromisoformat(x["due_date"])).days
        assert x["late_fee_rate_now"] == 50 * 2 ** (days_late // 7)                         # Rs. 50 a day, doubled for each full week late
    monkeypatch.setattr(settings, "fee_late_fee_max_per_day", 500)
    assert all(x["late_fee_rate_now"] <= 500 for x in await _rows(app_client, sid) if x["late_fee_rate_now"])       # the highest daily rate stops it
    assert (await app_client.post(f"/api/v1/fees/structures/{structure_id}/late-fee", json={"late_fee_per_day": 0, "reason": "No more fines"})).json()["late_fee_per_day"] == 0
    assert all(x["late_fee"] == 0 for x in await _rows(app_client, sid))