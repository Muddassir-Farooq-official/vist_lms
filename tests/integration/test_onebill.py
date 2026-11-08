"""1Bill: the bank network asks what is due on a consumer number and reports the payment."""
from __future__ import annotations

import base64
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.core.clock import institute_today
from app.core.config import settings
from app.fees import onebill as ob
from app.fees.models import FeeLedgerEntry, FeeStructure, LedgerEntryType
from app.fees.payment_models import ChallanStatus, OneBillTransaction, PaymentChallan
from app.fines.models import Fine, FineStatus
from tests.integration.test_fees import _create_active_fee_structure, _create_and_activate_student, _login_admin

pytestmark = pytest.mark.asyncio
API = "/api/v1/onebill"
H = {"username": "vist-1bill", "password": "s3cret-pass"}


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "one_bill_enabled", 1)
    monkeypatch.setattr(settings, "one_bill_username", H["username"])
    monkeypatch.setattr(settings, "one_bill_password", H["password"])
    monkeypatch.setattr(settings, "one_bill_allowed_ips", "")
    monkeypatch.setattr(settings, "one_bill_convenience_fixed", 50.0)
    monkeypatch.setattr(settings, "one_bill_convenience_percent", 1.0)
    monkeypatch.setattr(settings, "fee_late_fee_grace_days", 0)
    monkeypatch.setattr(settings, "fee_late_fee_cap", 0.0)
    monkeypatch.setattr(settings, "fee_late_fee_auto_post", 1)
    monkeypatch.setattr(settings, "fee_backdated_grace_days", 0)


async def _challan(client, db, *, due_in_days: int, late_per_day: float = 0.0, cnic="75101-0000001-1"):
    sid, _ = await _create_and_activate_student(client, cnic=cnic)
    structure_id = await _create_active_fee_structure(client, total_amount=10000, installment_count=1)
    await _login_admin(client)
    ch = (await db.execute(select(PaymentChallan).order_by(PaymentChallan.generated_at.desc()))).scalars().first()
    due = institute_today() + timedelta(days=due_in_days)
    await db.execute(update(PaymentChallan).where(PaymentChallan.id == ch.id).values(due_date=due))
    from datetime import datetime, timezone
    await db.execute(update(FeeLedgerEntry).where(FeeLedgerEntry.id == ch.fee_ledger_entry_id)
                     .values(due_date=due, created_at=datetime.now(timezone.utc) - timedelta(days=30)))   # created before it fell due
    await db.execute(update(FeeStructure).where(FeeStructure.id == uuid.UUID(structure_id)).values(late_fee_per_day=late_per_day))
    await db.commit()
    from types import SimpleNamespace
    return sid, SimpleNamespace(id=ch.id, consumer_number=ch.consumer_number, fee_assignment_id=ch.fee_assignment_id)


def _pay(ch, amount, auth="123456", day=None, bank="HBL"):
    return {"consumer_number": ch.consumer_number, "tran_auth_id": auth, "transaction_amount": ob.money(amount),
            "tran_date": (day or institute_today()).strftime("%Y%m%d"), "tran_time": "101500", "bank_mnemonic": bank, "reserved": ""}


async def test_switched_off_or_wrong_login_is_refused(app_client, monkeypatch):
    r = await app_client.post(f"{API}/inquiry", json={"consumer_number": "1"}, headers=H)
    assert r.status_code == 200 and r.json()["response_Code"] == ob.NOT_FOUND
    assert (await app_client.post(f"{API}/inquiry", json={"consumer_number": "1"}, headers={"username": "x", "password": "y"})).status_code == 401
    basic = base64.b64encode(f"{H['username']}:{H['password']}".encode()).decode()
    assert (await app_client.post(f"{API}/inquiry", json={"consumer_number": "1"}, headers={"Authorization": "Basic " + basic})).status_code == 200
    monkeypatch.setattr(settings, "one_bill_allowed_ips", "10.9.9.9")
    assert (await app_client.post(f"{API}/inquiry", json={"consumer_number": "1"}, headers=H)).status_code == 401
    monkeypatch.setattr(settings, "one_bill_allowed_ips", "")
    monkeypatch.setattr(settings, "one_bill_enabled", 0)
    assert (await app_client.post(f"{API}/inquiry", json={"consumer_number": "1"}, headers=H)).status_code == 503


async def test_inquiry_pay_once_and_the_same_payment_twice_changes_nothing(app_client, admin_user, db_session):
    sid, ch = await _challan(app_client, db_session, due_in_days=10)
    q = (await app_client.post(f"{API}/inquiry", json={"consumer_number": ch.consumer_number}, headers=H)).json()
    assert q["response_Code"] == ob.OK and q["bill_status"] == ob.UNPAID
    assert q["amount_within_dueDate"] == ob.money(10000 + 50 + 100) == "+0000001015000"     # Rs. 50 + 1%
    assert q["due_date"] == (institute_today() + timedelta(days=10)).strftime("%Y%m%d") and q["consumer_Detail"]

    wrong = (await app_client.post(f"{API}/payment", json=_pay(ch, 10000), headers=H)).json()
    assert wrong["response_Code"] == ob.INVALID                                                # without the charge: refused
    assert (await db_session.execute(select(func.count()).select_from(OneBillTransaction))).scalar_one() == 0

    ok = (await app_client.post(f"{API}/payment", json=_pay(ch, 10150), headers=H)).json()
    assert ok["response_Code"] == ob.OK and ok["Identification_parameter"]
    db_session.expire_all()
    paid = await db_session.get(PaymentChallan, ch.id)
    assert paid.status == ChallanStatus.PAID and paid.gateway_reference == "1BILL-HBL-123456"
    credit = await db_session.get(FeeLedgerEntry, paid.paid_entry_id)
    assert credit.entry_type == LedgerEntryType.CREDIT and float(credit.amount) == 10000 and credit.receipt_number == ok["Identification_parameter"]
    tx = (await db_session.execute(select(OneBillTransaction))).scalar_one()
    assert (float(tx.amount), float(tx.fee_amount), float(tx.late_fee_amount), float(tx.convenience_amount)) == (10150, 10000, 0, 150)

    again = (await app_client.post(f"{API}/payment", json=_pay(ch, 10150), headers=H)).json()
    assert again == ok                                                                         # same answer, nothing new
    other = (await app_client.post(f"{API}/payment", json=_pay(ch, 10150, auth="999999"), headers=H)).json()
    assert other["response_Code"] == ob.ALREADY_PAID
    credits = (await db_session.execute(select(func.count()).select_from(FeeLedgerEntry).where(
        FeeLedgerEntry.fee_assignment_id == ch.fee_assignment_id, FeeLedgerEntry.entry_type == LedgerEntryType.CREDIT))).scalar_one()
    assert credits == 1
    q = (await app_client.post(f"{API}/inquiry", json={"consumer_number": ch.consumer_number}, headers=H)).json()
    assert q["bill_status"] == ob.PAID and q["tran_auth_Id"] == "123456" and q["date_paid"] == institute_today().strftime("%Y%m%d")


async def test_a_late_payment_includes_the_late_fee_which_is_paid_as_a_fine(app_client, admin_user, db_session):
    sid, ch = await _challan(app_client, db_session, due_in_days=-5, late_per_day=100, cnic="75102-0000001-1")
    q = (await app_client.post(f"{API}/inquiry", json={"consumer_number": ch.consumer_number}, headers=H)).json()
    assert q["amount_within_dueDate"] == ob.money(10150)
    after = ob.parse_money(q["amount_after_dueDate"])
    assert after == 10150 + 500                                                                 # 5 days x Rs. 100
    assert (await app_client.post(f"{API}/payment", json=_pay(ch, 10150), headers=H)).json()["response_Code"] == ob.INVALID
    ok = (await app_client.post(f"{API}/payment", json=_pay(ch, after, auth="555001"), headers=H)).json()
    assert ok["response_Code"] == ob.OK
    db_session.expire_all()
    fines = (await db_session.execute(select(Fine).where(Fine.payer_id == uuid.UUID(sid)))).scalars().all()
    assert len(fines) == 1 and fines[0].status == FineStatus.PAID and float(fines[0].amount) == 500 and fines[0].receipt_number
    tx = (await db_session.execute(select(OneBillTransaction))).scalar_one()
    assert float(tx.late_fee_amount) == 500 and float(tx.convenience_amount) == 150 and tx.fine_receipt_numbers == fines[0].receipt_number

    rep = (await app_client.get("/api/v1/fees/onebill/report")).json()
    assert rep["count"] == 1 and rep["totals"] == {"amount": 10650.0, "fee": 10000.0, "late_fee": 500.0, "convenience": 150.0, "to_check": 0.0}
    assert rep["by_bank"] == [{"bank": "HBL", "count": 1, "amount": 10650.0}]


async def test_cancelled_challan_is_blocked_and_report_needs_accounts(app_client, admin_user, db_session):
    _sid, ch = await _challan(app_client, db_session, due_in_days=3, cnic="75103-0000001-1")
    await db_session.execute(update(PaymentChallan).where(PaymentChallan.id == ch.id).values(status=ChallanStatus.CANCELLED))
    await db_session.commit()
    q = (await app_client.post(f"{API}/inquiry", json={"consumer_number": ch.consumer_number}, headers=H)).json()
    assert q["response_Code"] == ob.BLOCKED and q["bill_status"] == ob.BLOCKED_STATUS
    assert (await app_client.post(f"{API}/payment", json=_pay(ch, 10150), headers=H)).json()["response_Code"] == ob.BLOCKED
    app_client.cookies.clear()
    assert (await app_client.get("/api/v1/fees/onebill/report")).status_code in (401, 403)


def test_money_and_date_formats():
    assert ob.money(1500) == "+0000000150000" and ob.money(0.5) == "+0000000000050"
    assert ob.parse_money("+0000000150000") == 1500.0 and ob.parse_money("1500.00") == 1500.0 and ob.parse_money("0000000150050") == 1500.5
    assert ob.parse_yyyymmdd("20261108").isoformat() == "2026-11-08" and ob.parse_yyyymmdd("bad") is None


async def test_the_daily_match_pages_render(app_client):
    for url in ("/dashboard/accounts/onebill", "/dashboard/admin/onebill"):
        r = await app_client.get(url, headers={"accept": "text/html"})
        assert r.status_code == 200 and "1Bill payments" in r.text and "onebill-report.js" in r.text
