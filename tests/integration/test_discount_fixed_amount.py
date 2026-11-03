"""A discount can be a percentage OR a fixed amount in rupees. A fixed amount comes off each fee it covers (e.g. Rs 10,000
off tuition every semester), spread over that fee's installments, and never more than what is owed on the fee."""
from __future__ import annotations

import pytest

from app.core.config import settings
from tests.integration.test_fees import _create_active_fee_structure, _create_and_activate_student, _login_admin

pytestmark = pytest.mark.asyncio
SC = "/api/v1/fees/scholarships"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _structure(client, fee_type_id, name, term, total, installments, start):
    r = await client.post("/api/v1/fees/structures", json={"fee_type_id": fee_type_id, "name": name, "term_label": term, "total_amount": total,
                                                          "installment_count": installments, "effective_from": start})
    assert r.status_code == 201, r.text
    assert (await client.post(f"/api/v1/fees/structures/{r.json()['id']}/activate")).status_code == 200
    return r.json()["id"]


async def _fees_of(client, sid):
    out = {}
    for a in (await client.get("/api/v1/fees/assignments")).json():
        if a["payer_id"] == sid:
            out[a["fee_structure_id"]] = (await client.get(f"/api/v1/fees/assignments/{a['id']}")).json()
    return out


async def test_a_fixed_amount_comes_off_tuition_every_semester_and_leaves_other_fees_alone(app_client, admin_user, db_session):
    sid, _ = await _create_and_activate_student(app_client, cnic="75001-0000001-1")
    fall = await _create_active_fee_structure(app_client, total_amount=90000, installment_count=2)
    tuition_type = (await app_client.get(f"/api/v1/fees/structures/{fall}")).json()["fee_type_id"]
    exam_type = (await app_client.post("/api/v1/fees/types", json={"code": "EXM", "name": "Exam Fee"})).json()["id"]
    exam = await _structure(app_client, exam_type, "Exam — Fall 2026", "Fall 2026", 5000, 1, "2026-09-01")

    too_much_pct = await app_client.post(SC, json={"student_id": sid, "name": "Bad", "kind": "PERCENT", "value": 150, "valid_from": "2026-01-01",
                                                   "reason": "Typing mistake"})
    assert too_much_pct.status_code == 422                                              # a percentage is at most 100
    r = await app_client.post(SC, json={"student_id": sid, "name": "Sibling discount", "kind": "FIXED", "value": 10000, "fee_type_id": tuition_type,
                                        "valid_from": "2026-01-01", "reason": "Two siblings enrolled"})
    assert r.status_code == 201, r.text
    fees = await _fees_of(app_client, sid)
    assert fees[fall]["total_concession"] == 10000.0 and fees[fall]["total_payable"] == 80000.0
    assert fees[exam]["total_concession"] == 0.0                                        # exam fee untouched
    from sqlalchemy import select

    from app.fees.models import FeeLedgerEntry, LedgerEntryType

    shares = (await db_session.execute(select(FeeLedgerEntry.installment_number, FeeLedgerEntry.amount).where(
        FeeLedgerEntry.fee_assignment_id == fees[fall]["id"], FeeLedgerEntry.entry_type == LedgerEntryType.CONCESSION))).all()
    assert sorted((n, float(a)) for n, a in shares) == [(1, 5000.0), (2, 5000.0)]        # spread over the two installments, written as ledger rows

    # Next semester's tuition is charged: the same Rs 10,000 comes off it too (not shared with Fall's).
    spring = await _structure(app_client, tuition_type, "Tuition — Spring 2027", "Spring 2027", 90000, 2, "2027-02-01")
    fees = await _fees_of(app_client, sid)
    assert fees[spring]["total_concession"] == 10000.0 and fees[fall]["total_concession"] == 10000.0
    listed = next(s for s in (await app_client.get(SC, params={"student_id": sid})).json() if s["name"] == "Sibling discount")
    assert listed["kind"] == "FIXED" and listed["value"] == 10000.0 and listed["given_so_far"] == 20000.0


async def test_a_fixed_amount_larger_than_the_fee_only_takes_off_what_is_owed(app_client, admin_user):
    sid, _ = await _create_and_activate_student(app_client, cnic="75001-0000002-2")
    fee_id = await _create_active_fee_structure(app_client, total_amount=6000, installment_count=3)
    await _login_admin(app_client)
    r = await app_client.post(SC, json={"student_id": sid, "name": "Full waiver", "kind": "FIXED", "value": 50000, "fee_structure_id": fee_id,
                                        "valid_from": "2026-01-01", "reason": "Hafiz-e-Quran fee waiver"})
    assert r.status_code == 201, r.text
    fee = (await _fees_of(app_client, sid))[fee_id]
    assert fee["total_concession"] == 6000.0 and fee["total_payable"] == 0.0           # never below zero


async def test_a_percentage_can_be_changed_to_a_fixed_amount(app_client, admin_user):
    sid, _ = await _create_and_activate_student(app_client, cnic="75001-0000003-3")
    fee_id = await _create_active_fee_structure(app_client, total_amount=40000, installment_count=2)
    await _login_admin(app_client)
    r = await app_client.post(SC, json={"student_id": sid, "name": "Merit", "kind": "PERCENT", "value": 25, "fee_structure_id": fee_id,
                                        "valid_from": "2026-01-01", "reason": "Merit list position 4"})
    assert r.status_code == 201, r.text
    assert (await _fees_of(app_client, sid))[fee_id]["total_concession"] == 10000.0
    ch = await app_client.patch(f"{SC}/{r.json()['id']}", json={"kind": "FIXED", "value": 8000, "note": "Policy: merit is a fixed Rs 8,000 now"})
    assert ch.status_code == 200, ch.text
    fee = (await _fees_of(app_client, sid))[fee_id]
    assert fee["total_concession"] == 8000.0 and fee["total_payable"] == 32000.0