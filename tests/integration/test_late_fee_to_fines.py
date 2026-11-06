"""A late fee shown to the student must reach the administration: it becomes a Fine when the late instalment is paid
(counter payment or challan), and the Fines screen shows the late fees still building up."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.fees.models import FeeLedgerEntry, LedgerEntryType
from app.fees.payment_models import PaymentChallan
from app.fines.models import Fine, FineSource
from tests.integration.test_fees import _create_active_fee_structure, _create_and_activate_student, _login_admin

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _rules(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "fee_late_fee_grace_days", 0)
    monkeypatch.setattr(settings, "fee_late_fee_cap", 0)
    monkeypatch.setattr(settings, "fee_backdated_grace_days", 0)
    monkeypatch.setattr(settings, "fee_late_fee_double_every_days", 0)
    monkeypatch.setattr(settings, "fee_late_fee_max_per_day", 0)
    monkeypatch.setattr(settings, "fee_late_fee_auto_post", 1)


async def _late_fee_account(client, db_session, cnic):
    sid, _ = await _create_and_activate_student(client, cnic=cnic)
    structure_id = await _create_active_fee_structure(client, total_amount=60000, installment_count=6)
    await _login_admin(client)
    assert (await client.post(f"/api/v1/fees/structures/{structure_id}/late-fee", json={"late_fee_per_day": 50, "reason": "Late fee policy"})).status_code == 200
    await db_session.execute(update(FeeLedgerEntry).where(FeeLedgerEntry.entry_type == LedgerEntryType.DEBIT)
                             .values(created_at=datetime(2026, 8, 1, tzinfo=timezone.utc)))     # the fee existed before the instalments fell due
    await db_session.commit()
    aid = (await client.get("/api/v1/fees/assignments")).json()[0]["id"]
    rows = sorted((await client.get(f"/api/v1/fees/accounts/students/{sid}")).json()["assignments"][0]["installments"], key=lambda r: r["installment_number"])
    return sid, aid, rows


async def _late_fines(db_session, sid):
    return (await db_session.execute(select(Fine).where(Fine.payer_id == sid, Fine.source == FineSource.FEE_LATE_PAYMENT))).scalars().all()


async def test_paying_a_late_instalment_at_the_counter_puts_its_late_fee_in_fines(app_client, admin_user, db_session):
    sid, aid, rows = await _late_fee_account(app_client, db_session, "75201-0000001-1")
    first = rows[0]
    assert first["late_fee"] > 0 and await _late_fines(db_session, sid) == []          # shown, but not a fine yet
    r = await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": first["remaining"], "method": "CASH"})
    assert r.status_code == 201, r.text
    assert r.json()["late_fee_posted"] == first["late_fee"]
    fines = await _late_fines(db_session, sid)
    assert len(fines) == 1 and float(fines[0].amount) == first["late_fee"]
    # the next payment settles instalment 2: its own late fee is added, the first one is not charged again
    second = rows[1]
    r2 = (await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": second["remaining"], "method": "CASH"})).json()
    assert r2["late_fee_posted"] == second["late_fee"]
    assert sorted(float(f.amount) for f in await _late_fines(db_session, sid)) == sorted([first["late_fee"], second["late_fee"]])
    listed = (await app_client.get("/api/v1/fines", params={"payer_type": "STUDENT"})).json()
    assert sum(1 for f in listed if f["source"] == "FEE_LATE_PAYMENT" and f["payer_id"] == str(sid)) == 2     # visible in the Fines section


async def test_confirming_a_late_challan_also_puts_its_late_fee_in_fines(app_client, admin_user, db_session):
    sid, aid, rows = await _late_fee_account(app_client, db_session, "75201-0000002-1")
    first = rows[0]
    ch = (await db_session.execute(select(PaymentChallan).where(PaymentChallan.fee_ledger_entry_id == first["ledger_entry_id"],
                                                                PaymentChallan.status == "PENDING"))).scalars().first()
    if ch is None:
        made = await app_client.post(f"/api/v1/fees/assignments/{aid}/installments/{first['ledger_entry_id']}/challan")
        assert made.status_code in (200, 201), made.text
        ch_id = made.json()["id"]
    else:
        ch_id = ch.id
    r = await app_client.post(f"/api/v1/fees/challans/{ch_id}/mark-paid", json={"method": "CASH"})
    assert r.status_code == 200, r.text
    assert r.json()["late_fee_posted"] == first["late_fee"] and len(await _late_fines(db_session, sid)) == 1


async def test_the_setting_off_keeps_the_old_behaviour(app_client, admin_user, db_session, monkeypatch):
    sid, aid, rows = await _late_fee_account(app_client, db_session, "75201-0000003-1")
    monkeypatch.setattr(settings, "fee_late_fee_auto_post", 0)
    r = (await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": rows[0]["remaining"], "method": "CASH"})).json()
    assert r["late_fee_posted"] is None and await _late_fines(db_session, sid) == []


async def test_fines_screen_shows_late_fees_building_up_and_can_post_them(app_client, admin_user, db_session):
    sid, aid, rows = await _late_fee_account(app_client, db_session, "75201-0000004-1")
    owed_late = round(sum(r["late_fee"] for r in rows if r["remaining"] > 0), 2)
    b = (await app_client.get("/api/v1/fines/late-fees-building")).json()
    me = next(p for p in b["list"] if p["payer_id"] == str(sid))
    assert b["total"] >= owed_late > 0 and me["amount"] == owed_late and me["rate_now"] > 0
    posted = (await app_client.post("/api/v1/fines/late-fees-building/post-all")).json()
    assert posted["total"] >= owed_late
    assert round(sum(float(f.amount) for f in await _late_fines(db_session, sid)), 2) == owed_late
    after = (await app_client.get("/api/v1/fines/late-fees-building")).json()
    assert all(p["payer_id"] != str(sid) for p in after["list"])
    # paying later adds nothing for the days already posted (same day): no double charge
    r = (await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": rows[0]["remaining"], "method": "CASH"})).json()
    assert r["late_fee_posted"] is None