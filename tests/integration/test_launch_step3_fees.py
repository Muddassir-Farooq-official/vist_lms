"""Step 3 fee guards.

- payments need a method, a reference (unless cash) and a paid-on date, and get a receipt number + PDF
- overpayment is refused; a challan already covered another way cannot be marked paid
- an instalment created after its due date does not accrue a backdated late fee
- simulated payments are refused on the live (production) system
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.clock import institute_today
from app.core.config import settings
from app.fees.assignment_service import FeeStatusCalculator
from app.fees.models import FeeLedgerEntry, LedgerEntryType
from tests.integration.test_fees import _create_active_fee_structure, _create_and_activate_student, _login, _login_admin

pytestmark = pytest.mark.asyncio
SP = "StudentPass123!"


async def _student_with_fee(client, cnic, total=10000, n=2):
    sid, user = await _create_and_activate_student(client, cnic=cnic)
    await _create_active_fee_structure(client, total_amount=total, installment_count=n)
    await _login_admin(client)
    assignment = next(a for a in (await client.get("/api/v1/fees/assignments")).json() if a["payer_id"] == sid)
    return sid, user, assignment["id"]


async def test_payment_needs_reference_unless_cash_and_gets_a_receipt(app_client, admin_user):
    sid, user, aid = await _student_with_fee(app_client, "73001-0000001-1")
    no_ref = await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 1000, "method": "BANK_DEPOSIT"})
    assert no_ref.status_code == 422 and "reference" in no_ref.text
    no_method = await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 1000})
    assert no_method.status_code == 422
    future = await app_client.post(f"/api/v1/fees/assignments/{aid}/payments",
                                   json={"amount": 1000, "method": "CASH", "paid_on": str(institute_today() + timedelta(days=1))})
    assert future.status_code == 422 and "future" in future.text
    paid_day = institute_today() - timedelta(days=2)
    ok = await app_client.post(f"/api/v1/fees/assignments/{aid}/payments",
                               json={"amount": 3000, "method": "BANK_DEPOSIT", "reference": "MCB-778812", "paid_on": str(paid_day)})
    assert ok.status_code == 201, ok.text
    credit = next(e for e in (await app_client.get(f"/api/v1/fees/assignments/{aid}/ledger")).json() if e["entry_type"] == "CREDIT")
    assert credit["payment_method"] == "BANK_DEPOSIT" and credit["payment_reference"] == "MCB-778812"
    assert credit["paid_on"] == str(paid_day) and credit["receipt_number"].startswith(f"RCPT-{institute_today().year}-")
    staff_pdf = await app_client.get(f"/api/v1/fees/receipts/{credit['id']}/pdf")
    assert staff_pdf.status_code == 200 and staff_pdf.content[:4] == b"%PDF"
    await _login(app_client, user, SP)
    acct = (await app_client.get("/api/v1/fees/accounts/me")).json()
    assert acct["payments"][0]["receipt_number"] == credit["receipt_number"]
    assert (await app_client.get(f"/api/v1/fees/receipts/{credit['id']}/pdf")).status_code == 200


async def test_receipt_numbers_are_unique_and_other_students_cannot_open_a_receipt(app_client, admin_user):
    _s1, _u1, a1 = await _student_with_fee(app_client, "73001-0000002-2")
    for _ in range(2):
        assert (await app_client.post(f"/api/v1/fees/assignments/{a1}/payments", json={"amount": 100, "method": "CASH"})).status_code == 201
    credits = [e for e in (await app_client.get(f"/api/v1/fees/assignments/{a1}/ledger")).json() if e["entry_type"] == "CREDIT"]
    assert len({c["receipt_number"] for c in credits}) == 2
    _s2, u2 = await _create_and_activate_student(app_client, cnic="73001-0000003-3")
    await _login(app_client, u2, SP)
    assert (await app_client.get(f"/api/v1/fees/receipts/{credits[0]['id']}/pdf")).status_code == 404


async def test_overpayment_is_refused(app_client, admin_user):
    _sid, _user, aid = await _student_with_fee(app_client, "73001-0000004-4", total=8000, n=2)
    over = await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 500000, "method": "CASH"})
    assert over.status_code == 422 and "more than what is still owed" in over.text
    assert over.json()["error"]["details"]["outstanding"] == 8000.0
    assert (await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 8000, "method": "CASH"})).status_code == 201
    again = await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 1, "method": "CASH"})
    assert again.status_code == 422 and "already fully paid" in again.text


async def test_challan_already_covered_another_way_cannot_be_marked_paid(app_client, admin_user):
    _sid, user, aid = await _student_with_fee(app_client, "73001-0000005-5", total=6000, n=1)
    await _login(app_client, user, SP)
    challan = (await app_client.get("/api/v1/fees/accounts/me")).json()["assignments"][0]["installments"][0]["challan"]
    await _login_admin(app_client)
    assert (await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 6000, "method": "CASH"})).status_code == 201
    r = await app_client.post(f"/api/v1/fees/challans/{challan['id']}/mark-paid", json={"method": "BANK_DEPOSIT", "reference": "HBL-9"})
    # the counter payment settled the installment, so its challan was cancelled at once and cannot collect twice
    assert r.status_code == 422 and "cancelled" in r.text


async def test_staff_mark_paid_records_the_bank_details(app_client, admin_user):
    _sid, user, aid = await _student_with_fee(app_client, "73001-0000006-6", total=6000, n=1)
    await _login(app_client, user, SP)
    challan = (await app_client.get("/api/v1/fees/accounts/me")).json()["assignments"][0]["installments"][0]["challan"]
    await _login_admin(app_client)
    r = await app_client.post(f"/api/v1/fees/challans/{challan['id']}/mark-paid",
                              json={"method": "BANK_DEPOSIT", "reference": "UBL-5512", "paid_on": str(institute_today() - timedelta(days=1))})
    assert r.status_code == 200, r.text
    credit = next(e for e in (await app_client.get(f"/api/v1/fees/assignments/{aid}/ledger")).json() if e["entry_type"] == "CREDIT")
    assert (credit["payment_reference"], credit["payment_method"]) == ("UBL-5512", "BANK_DEPOSIT") and credit["receipt_number"]


async def test_simulated_payment_is_refused_in_production(app_client, admin_user, monkeypatch):
    _sid, user, _aid = await _student_with_fee(app_client, "73001-0000007-7", total=6000, n=1)
    await _login(app_client, user, SP)
    challan = (await app_client.get("/api/v1/fees/accounts/me")).json()["assignments"][0]["installments"][0]["challan"]
    await _login_admin(app_client)
    monkeypatch.setattr(settings, "app_env", "production")
    r = await app_client.post(f"/api/v1/fees/challans/{challan['id']}/simulate-payment")
    assert r.status_code == 403 and "switched off" in r.text


# ------------------------------------------------------------------ calculator (no database)
def _debit(amount, due, created):
    import uuid
    return FeeLedgerEntry(id=uuid.uuid4(), entry_type=LedgerEntryType.DEBIT, amount=amount, due_date=due, installment_number=1,
                          created_at=datetime(created.year, created.month, created.day, 9, tzinfo=timezone.utc), late_fee_charged_amount=0)


def test_backdated_instalment_has_no_late_fee_until_grace_after_creation():
    today = date(2026, 10, 20)
    created = date(2026, 10, 6)                     # activated in October ...
    entries = [_debit(30000, date(2026, 9, 1), created)]   # ... for an instalment due 1 September
    row = FeeStatusCalculator.installments(entries, 100, as_of=today)[0]
    # late fee runs from 6 Oct + 7 grace days = 13 Oct, not from 1 Sep
    assert row["status"] == "OVERDUE" and row["late_fee"] == (today - (created + timedelta(days=settings.fee_backdated_grace_days))).days * 100
    assert FeeStatusCalculator.installments(entries, 100, as_of=date(2026, 10, 10))[0]["late_fee"] == 0


def test_payment_counts_from_the_day_it_was_paid_not_the_day_it_was_typed_in():
    import uuid
    due = date(2026, 9, 10)
    debit = _debit(5000, due, date(2026, 8, 1))
    credit = FeeLedgerEntry(id=uuid.uuid4(), entry_type=LedgerEntryType.CREDIT, amount=5000, paid_on=date(2026, 9, 8),
                            created_at=datetime(2026, 9, 20, tzinfo=timezone.utc))   # paid on time, recorded 12 days later
    row = FeeStatusCalculator.installments([debit, credit], 100, as_of=date(2026, 10, 1))[0]
    assert row["status"] == "PAID" and row["late_fee"] == 0