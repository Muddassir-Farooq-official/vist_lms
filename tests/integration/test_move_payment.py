"""A payment recorded on the wrong fee is moved to the right fee of the same student: the accounts office asks, an
administrator approves. The receipt keeps its number; the right fee counts as paid on the original day; the wrong fee
gets no fine because of the move."""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.audit.models import AuditLog
from app.core.clock import institute_today
from app.core.config import settings
from app.fees.models import FeeLedgerEntry, LedgerEntryType
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _login_admin
from tests.integration.test_fees import _create_and_activate_student

pytestmark = pytest.mark.asyncio
F = "/api/v1/fees"


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    for name, value in (("fee_late_fee_grace_days", 0), ("fee_late_fee_cap", 0), ("fee_backdated_grace_days", 0), ("fee_late_fee_double_every_days", 0),
                        ("fee_late_fee_max_per_day", 0), ("fee_late_fee_auto_post", 0), ("fee_payment_max_backdate_days", 400)):
        monkeypatch.setattr(settings, name, value)


async def _fee(client, code, name, due):
    t = (await client.post(f"{F}/types", json={"code": code, "name": name})).json()["id"]
    s = await client.post(f"{F}/structures", json={"fee_type_id": t, "name": name, "term_label": "Fall 2026", "total_amount": 5000,
                                                    "installment_count": 1, "effective_from": str(due), "late_fee_per_day": 100})
    assert s.status_code == 201, s.text
    assert (await client.post(f"{F}/structures/{s.json()['id']}/activate")).status_code == 200


async def _account(client, sid) -> dict:
    body = (await client.get(f"{F}/accounts/students/{sid}")).json()
    return {a["fee_name"]: a for a in body["assignments"]}


async def test_a_payment_on_the_wrong_fee_is_moved_after_approval(app_client, admin_user, db_session):
    from pypdf import PdfReader

    sid, _ = await _create_and_activate_student(app_client, cnic="75201-0000051-1")
    await _login_admin(app_client)
    due = institute_today() - timedelta(days=20)
    await _fee(app_client, "TUIM", "Tuition", due)
    await _fee(app_client, "HOSM", "Hostel", due)
    acc = await _account(app_client, sid)
    tuition, hostel = acc["Tuition"]["id"], acc["Hostel"]["id"]
    # the charges were raised before they fell due (as on the live system), so late days count from the due date
    await db_session.execute(update(FeeLedgerEntry).where(FeeLedgerEntry.entry_type == LedgerEntryType.DEBIT,
                                                          FeeLedgerEntry.fee_assignment_id.in_([uuid.UUID(tuition), uuid.UUID(hostel)]))
                             .values(created_at=datetime.now(timezone.utc) - timedelta(days=40)))
    await db_session.commit()

    # tuition money, recorded on hostel by mistake (paid on the due day: no fine)
    paid = await app_client.post(f"{F}/assignments/{hostel}/payments", json={"amount": 5000, "method": "CASH", "paid_on": str(due), "late_fine": "REMOVE", "late_fine_reason": "Paid on the due day"})
    assert paid.status_code == 201, paid.text
    lines = (await app_client.get(f"{F}/assignments/{hostel}/statement")).json()["lines"]
    credit = next(x for x in lines if x["type"] == "CREDIT")
    acc = await _account(app_client, sid)
    assert acc["Hostel"]["remaining"] == 0 and acc["Tuition"]["remaining"] == 5000 and acc["Tuition"]["late_fee_accrued"] > 0

    # the accounts office asks; nothing changes yet
    user, pw = await _staff(app_client, db_session, "ACM", "ACCOUNTS_OFFICER", "35202-7100151-3")
    await _first_login(app_client, user, pw)
    short = await app_client.post(f"{F}/assignments/payments/{credit['id']}/move", json={"to_assignment_id": tuition, "reason": "oops"})
    assert short.status_code == 422
    ask = await app_client.post(f"{F}/assignments/payments/{credit['id']}/move",
                                json={"to_assignment_id": tuition, "reason": "Tuition paid, entered on hostel by mistake"})
    assert ask.status_code == 200, ask.text
    assert ask.json()["pending_approval"] is True
    again = await app_client.post(f"{F}/assignments/payments/{credit['id']}/move", json={"to_assignment_id": tuition, "reason": "Asked twice by mistake"})
    assert again.status_code in (409, 422) and "already waiting" in again.text
    assert (await _account(app_client, sid))["Hostel"]["remaining"] == 0

    # the administrator approves
    await _login_admin(app_client)
    d = await app_client.post(f"/api/v1/approvals/{ask.json()['approval_request_id']}/decide", json={"approve": True, "note": "Checked the slip"})
    assert d.status_code == 200, d.text
    assert d.json()["result"]["receipt"] == credit["receipt_number"]

    acc = await _account(app_client, sid)
    assert acc["Tuition"]["remaining"] == 0 and acc["Tuition"]["late_fee_accrued"] == 0          # paid on the original day
    assert acc["Hostel"]["remaining"] == 5000 and acc["Hostel"]["late_fee_accrued"] == 0          # owed again, no fine for the move
    assert acc["Hostel"]["late_fee_paused_until"] == str(institute_today() + timedelta(days=7))

    lines = (await app_client.get(f"{F}/assignments/{hostel}/statement")).json()["lines"]
    assert next(x for x in lines if x["id"] == credit["id"])["moved"] is True
    assert any(x["type"] == "CREDIT_OUT" for x in lines) and lines[-1]["balance"] == 5000
    t_lines = (await app_client.get(f"{F}/assignments/{tuition}/statement")).json()["lines"]
    assert any(x["type"] == "CREDIT_IN" and x["what"] == "Brought from another fee" for x in t_lines)

    pdf = await app_client.get(f"{F}/receipts/{credit['id']}/pdf")
    assert pdf.status_code == 200
    text = PdfReader(io.BytesIO(pdf.content)).pages[0].extract_text()
    assert credit["receipt_number"] in text and "Tuition" in text and "moved from Hostel" in text

    # a moved payment cannot be reversed or moved again
    rev = await app_client.post(f"{F}/assignments/payments/{credit['id']}/reverse", json={"reason": "Trying to reverse"})
    assert rev.status_code in (409, 422) and "moved to another fee" in rev.text
    mv = await app_client.post(f"{F}/assignments/payments/{credit['id']}/move", json={"to_assignment_id": tuition, "reason": "Move it once more"})
    assert mv.status_code in (409, 422)
    assert (await db_session.execute(select(AuditLog).where(AuditLog.action == "fee.payment_moved"))).scalars().one()
