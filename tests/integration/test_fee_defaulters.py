"""Fee defaulter follow-up (app/fees/defaulters.py): the list by batch term with day / amount filters and CSV, SMS sent
on purpose (preview, students and/or parents, at most one fee SMS per student a day), promises to pay (no reminder and
no admit-card fee hold until then) and admit-card exemptions for fees (administrators only)."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import institute_today
from app.core.config import settings
from app.exams.service import ExamScheduleService
from app.fees.models import FeeAssignment, FeeLedgerEntry, LedgerEntryType, PayerType
from app.sms.models import SmsMessage
from app.sms.service import SmsService
from tests.integration.test_attendance_and_grading import _login_admin
from tests.integration.test_fees import _create_accounts_officer, _create_active_fee_structure
from tests.integration.test_result_sheets import _class

pytestmark = pytest.mark.asyncio
F = "/api/v1/fee-defaulters"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(settings, "admit_card_requires_fees_cleared", 1)


async def _due(db, student_id, due):
    """Move the student's fee instalment to this due date (to make it overdue, or not yet due)."""
    ids = (await db.execute(select(FeeAssignment.id).where(FeeAssignment.payer_type == PayerType.STUDENT, FeeAssignment.payer_id == student_id))).scalars().all()
    rows = (await db.execute(select(FeeLedgerEntry).where(FeeLedgerEntry.fee_assignment_id.in_(ids), FeeLedgerEntry.entry_type == LedgerEntryType.DEBIT))).scalars().all()
    assert rows
    for e in rows:
        e.due_date = due
    await db.flush()


async def _fee_held(db, student_id) -> bool:
    db.expire_all()
    return any("overdue fees" in w for w in await ExamScheduleService(db).withheld(student_id))


async def test_defaulter_list_sms_promises_and_exemptions(app_client, admin_user, db_session):
    _offering, students, _a, _t = await _class(app_client, db_session, code="FDF", n=3, with_teacher=False)
    s1, s2, s3 = (uuid.UUID(s[0]) for s in students)
    await _login_admin(app_client)
    from app.students.models import Student

    program_id = (await db_session.get(Student, s1)).program_id
    today = institute_today()
    term = (await app_client.post("/api/v1/academics/academic-terms", json={"label": "FDF 1st Fall 2026", "start_date": str(today - timedelta(days=60)),
                                                                            "end_date": str(today + timedelta(days=60)), "program_id": str(program_id),
                                                                            "semester": 1})).json()["id"]
    await _create_active_fee_structure(app_client, total_amount=6000, installment_count=1)
    await _due(db_session, s1, today - timedelta(days=40))
    await _due(db_session, s2, today - timedelta(days=10))
    await _due(db_session, s3, today + timedelta(days=30))                       # not due yet

    # the list: anyone overdue in the batch, filtered by days and amount
    d = (await app_client.get(F, params={"term_id": term})).json()
    assert {r["student_id"] for r in d["students"]} == {str(s1), str(s2)} and d["count"] == 2
    assert {r["student_id"] for r in (await app_client.get(F, params={"term_id": term, "min_days": 30})).json()["students"]} == {str(s1)}
    assert (await app_client.get(F, params={"term_id": term, "min_amount": 7000})).json()["count"] == 0
    csv = await app_client.get(f"{F}/export.csv", params={"term_id": term})
    assert csv.status_code == 200 and "Promised to pay by" in csv.text.splitlines()[0] and csv.text.count("\n") == 3
    choices = (await app_client.get(f"{F}/choices")).json()
    assert any(t["id"] == term for t in choices["terms"]) and "{amount}" in choices["default_message"]
    assert await _fee_held(db_session, s1) and await _fee_held(db_session, s2) and not await _fee_held(db_session, s3)

    # SMS: preview first (nothing queued), then send; a second send the same day is skipped
    ids = [str(s1), str(s2), str(s3)]
    preview = await app_client.post(f"{F}/remind", json={"student_ids": ids, "to": "STUDENT", "message": "Dear {name}, Rs. {amount} is due since {since}.", "dry_run": True})
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert p["students"] == 2 and p["messages"] == 2 and p["skipped"] == {"no overdue fee": 1} and "is due since" in p["sample"] and "{" not in p["sample"]
    sms = lambda: db_session.execute(select(SmsMessage).where(SmsMessage.student_id.in_([s1, s2, s3])))   # noqa: E731
    assert (await sms()).scalars().all() == []
    sent = (await app_client.post(f"{F}/remind", json={"student_ids": ids, "to": "STUDENT", "message": "Dear {name}, Rs. {amount} is due.", "dry_run": False})).json()
    assert sent["messages"] == 2
    rows = (await sms()).scalars().all()
    assert {(m.student_id, m.event, m.recipient) for m in rows} == {(s1, "FEE_DEFAULTER", "STUDENT"), (s2, "FEE_DEFAULTER", "STUDENT")}
    again = (await app_client.post(f"{F}/remind", json={"student_ids": ids, "to": "BOTH", "dry_run": False})).json()
    assert again["messages"] == 0 and again["skipped"]["already sent a fee SMS today"] == 2
    assert all(r["reminded_today"] for r in (await app_client.get(F, params={"term_id": term})).json()["students"])
    # nor does the daily automatic reminder send another one today
    await SmsService(db_session).queue_fee_overdue_reminders()
    assert {m.event for m in (await sms()).scalars().all()} == {"FEE_DEFAULTER"}
    bad = await app_client.post(f"{F}/remind", json={"student_ids": ids, "to": "NOBODY"})
    assert bad.status_code == 422

    # promise to pay: no hold and no reminder until the date; a new promise replaces the old one; cancelling restores both
    assert (await app_client.post(f"{F}/promises", json={"student_id": str(s1), "promised_date": str(today - timedelta(days=1))})).status_code == 422
    assert (await app_client.post(f"{F}/promises", json={"student_id": str(s1), "promised_date": str(today + timedelta(days=120))})).status_code == 422
    first = await app_client.post(f"{F}/promises", json={"student_id": str(s1), "promised_date": str(today + timedelta(days=3)), "amount": 3000, "note": "Father called"})
    assert first.status_code == 201, first.text
    second = await app_client.post(f"{F}/promises", json={"student_id": str(s1), "promised_date": str(today + timedelta(days=7))})
    assert second.status_code == 201
    mine = [x for x in (await app_client.get(f"{F}/promises", params={"include_old": "true"})).json() if x["student_id"] == str(s1)]
    assert sorted((x["active"], x["promised_date"]) for x in mine) == [(False, str(today + timedelta(days=3))), (True, str(today + timedelta(days=7)))]
    assert not await _fee_held(db_session, s1) and await _fee_held(db_session, s2)
    row = next(r for r in (await app_client.get(F, params={"term_id": term})).json()["students"] if r["student_id"] == str(s1))
    assert row["promise"]["promised_date"] == str(today + timedelta(days=7))
    await db_session.execute(SmsMessage.__table__.delete().where(SmsMessage.student_id.in_([s1, s2])))      # a new day
    skip = (await app_client.post(f"{F}/remind", json={"student_ids": [str(s1), str(s2)], "to": "STUDENT", "dry_run": True})).json()
    assert skip["skipped"] == {"promised to pay": 1} and skip["students"] == 1
    assert (await app_client.post(f"{F}/promises/{second.json()['id']}/cancel")).status_code == 200
    assert (await app_client.post(f"{F}/promises/{second.json()['id']}/cancel")).status_code == 422
    assert await _fee_held(db_session, s1)

    # exemption from the admit-card fee hold: administrators only, with a reason; revoking keeps the record
    ex = await app_client.post(f"{F}/exemptions", json={"student_id": str(s2), "reason": "Scholarship approved, not posted yet", "until": str(today + timedelta(days=14))})
    assert ex.status_code == 201, ex.text
    assert not await _fee_held(db_session, s2)
    assert next(r for r in (await app_client.get(F, params={"term_id": term})).json()["students"] if r["student_id"] == str(s2))["exempt"] is True
    assert (await app_client.post(f"{F}/exemptions/{ex.json()['id']}/revoke")).status_code == 200
    assert await _fee_held(db_session, s2)
    old = [e for e in (await app_client.get(f"{F}/exemptions", params={"include_old": "true"})).json() if e["student_id"] == str(s2)]
    assert len(old) == 1 and old[0]["active"] is False

    # the accounts office follows up (list, SMS, promises) but cannot exempt
    await _create_accounts_officer(app_client, db_session, code="FDFACC")
    assert (await app_client.get(F, params={"term_id": term})).status_code == 200
    assert (await app_client.post(f"{F}/promises", json={"student_id": str(s2), "promised_date": str(today + timedelta(days=2))})).status_code == 201
    refused = await app_client.post(f"{F}/exemptions", json={"student_id": str(s2), "reason": "Please allow"})
    assert refused.status_code == 403
    page = await app_client.get("/dashboard/accounts/fee-defaulters")
    assert page.status_code == 200 and "fee-defaulters.js" in page.text and 'data-can-exempt="no"' in page.text
