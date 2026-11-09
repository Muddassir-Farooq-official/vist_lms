"""Batch 3: certificates students ask for (approve -> pay -> download) and the principal's monthly report."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.notifications.models import Notification
from tests.integration.test_attendance_and_grading import _login, _login_admin, _setup_offering_with_enrolled_students

pytestmark = pytest.mark.asyncio
PW = "StudentPass123!"
ME = "/api/v1/records/me/certificate-requests"
OFFICE = "/api/v1/records/certificate-requests"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _student(app_client, code: str, cnic_base: int):
    _off, studs = await _setup_offering_with_enrolled_students(app_client, code=code, n_students=1, cnic_base=cnic_base)
    sid, username, _ = studs[0]
    return sid, username


async def _ask(app_client, username, kind, purpose="Bank account opening"):
    await _login(app_client, username, PW)
    r = await app_client.post(ME, json={"kind": kind, "purpose": purpose})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_student_asks_office_approves_student_pays_then_downloads(app_client, admin_user, db_session):
    sid, username = await _student(app_client, "CR1", 8300)
    await _login(app_client, username, PW)
    first = (await app_client.get(ME)).json()
    assert first["requests"] == [] and {k["kind"] for k in first["kinds"]} == {"BONAFIDE", "CHARACTER", "COMPLETION", "PROVISIONAL", "MIGRATION", "FEE_CLEARANCE"}
    assert (await app_client.post(ME, json={"kind": "PASSPORT", "purpose": "Travel"})).status_code == 422
    rid = await _ask(app_client, username, "BONAFIDE")
    again = await app_client.post(ME, json={"kind": "BONAFIDE", "purpose": "Another"})
    assert again.status_code in (400, 409, 422) and "already asked" in again.text
    assert (await app_client.get(OFFICE)).status_code == 403                                  # students cannot see the office list

    await _login_admin(app_client)
    office = (await app_client.get(OFFICE, params={"status": "OPEN"})).json()
    item = next(x for x in office["requests"] if x["id"] == rid)
    assert item["status"] == "REQUESTED" and item["problems"] == [] and item["fee"] == settings.certificate_fee_bonafide
    note = (await db_session.execute(select(Notification).where(Notification.title == "Certificate requested"))).scalars().first()
    assert note is not None and note.link == "/dashboard/admin/certificates"

    r = await app_client.post(f"{OFFICE}/{rid}/approve", json={"fee": 450})
    assert r.status_code == 200 and r.json()["status"] == "AWAITING_FEE"
    assert (await app_client.post(f"{OFFICE}/{rid}/approve", json={})).status_code in (400, 409, 422)   # answered once only

    await _login(app_client, username, PW)
    mine = (await app_client.get(ME)).json()["requests"][0]
    assert mine["status"] == "AWAITING_FEE" and mine["challan"]["amount"] == 450 and mine["challan"]["consumer_number"]
    assert (await app_client.get(f"{ME}/{rid}/pdf")).status_code in (400, 409, 422)        # not before it is paid
    account = (await app_client.get("/api/v1/fees/accounts/me")).json()                    # an ordinary charge on the fee account
    assert "Certificate fees" in str(account)

    await _login_admin(app_client)
    paid = await app_client.post(f"/api/v1/fees/challans/{mine['challan']['id']}/simulate-payment")
    assert paid.status_code == 200, paid.text

    await _login(app_client, username, PW)
    mine = (await app_client.get(ME)).json()["requests"][0]
    assert mine["status"] == "READY" and mine["reference"].startswith("BF-")
    pdf = await app_client.get(f"{ME}/{rid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    again = await app_client.get(f"{ME}/{rid}/pdf")                                           # the same paper every time
    assert again.status_code == 200
    code = mine["verify_url"].rsplit("/", 1)[-1]
    v = (await app_client.get(f"/api/v1/records/verify/{code}")).json()
    assert v["kind"] == "BONAFIDE" and v["revoked"] is False
    ready = (await db_session.execute(select(Notification).where(Notification.title == "Your certificate is ready"))).scalars().all()
    assert len(ready) == 1 and ready[0].link == "/dashboard/student/certificates"


async def test_checks_are_made_on_approval_and_an_unpaid_request_can_be_cancelled(app_client, admin_user, db_session):
    _sid, username = await _student(app_client, "CR2", 8310)
    first = await _ask(app_client, username, "CHARACTER", "Job application")
    await _login_admin(app_client)
    assert (await app_client.post(f"{OFFICE}/{first}/approve", json={"fee": 300})).json()["status"] == "AWAITING_FEE"

    clear = await _ask(app_client, username, "FEE_CLEARANCE", "Scholarship form")
    await _login_admin(app_client)
    item = next(x for x in (await app_client.get(OFFICE)).json()["requests"] if x["id"] == clear)
    assert any("Fees not yet due" in p for p in item["problems"])                # the unpaid certificate fee
    refused = await app_client.post(f"{OFFICE}/{clear}/approve", json={})
    assert refused.status_code in (400, 409, 422) and "anyway" in refused.text
    assert (await app_client.post(f"{OFFICE}/{clear}/approve", json={"override_reason": "short"})).status_code in (400, 409, 422)

    # the office cancels the unpaid character certificate: what was owed is written off, the charge stays on record
    assert (await app_client.post(f"{OFFICE}/{first}/cancel", json={"note": "Asked by mistake"})).json()["status"] == "CANCELLED"
    from app.fees.models import FeeLedgerEntry, LedgerEntryType
    from app.records.models import CertificateRequest

    req = await db_session.get(CertificateRequest, uuid.UUID(first))
    entries = (await db_session.execute(select(FeeLedgerEntry).where(FeeLedgerEntry.related_entry_id == req.fee_ledger_entry_id))).scalars().all()
    assert [(e.entry_type, float(e.amount)) for e in entries] == [(LedgerEntryType.CONCESSION, 300.0)]
    assert await db_session.get(FeeLedgerEntry, req.fee_ledger_entry_id) is not None

    ok = await app_client.post(f"{OFFICE}/{clear}/approve", json={})                          # now clear, and free by default
    assert ok.status_code == 200 and ok.json()["status"] == "READY"
    pdf = await app_client.get(f"{OFFICE}/{clear}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


async def test_refuse_and_cancel_before_an_answer(app_client, admin_user, db_session):
    _sid, username = await _student(app_client, "CR3", 8320)
    a = await _ask(app_client, username, "MIGRATION", "Moving to another college")
    b = await _ask(app_client, username, "BONAFIDE", "Visa")
    assert (await app_client.post(f"{ME}/{b}/cancel")).json()["status"] == "CANCELLED"
    await _login_admin(app_client)
    assert (await app_client.post(f"{OFFICE}/{a}/reject", json={"note": "x"})).status_code == 422
    assert (await app_client.post(f"{OFFICE}/{a}/reject", json={"note": "Please clear the library first"})).json()["status"] == "REJECTED"
    await _login(app_client, username, PW)
    rows = {r["id"]: r for r in (await app_client.get(ME)).json()["requests"]}
    assert rows[a]["status"] == "REJECTED" and rows[a]["note"] == "Please clear the library first" and rows[b]["status"] == "CANCELLED"
    assert (await app_client.post(f"{ME}/{a}/cancel")).status_code in (400, 409, 422)
    assert (await app_client.get("/dashboard/student/certificates", headers={"accept": "text/html"})).status_code == 200


async def test_monthly_report_on_screen_as_pdf_and_sent_once_on_the_first(app_client, admin_user, db_session, monkeypatch):
    sid, username = await _student(app_client, "MR1", 8330)
    await _login_admin(app_client)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    r = await app_client.get("/api/v1/reports/monthly", params={"month": month})
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) >= {"fees", "classes", "results", "staff", "month_label", "complete"}
    assert {"received", "outstanding", "overdue", "by_head", "top_defaulters"} <= set(d["fees"])
    assert {"teachers", "classes", "held", "average"} <= set(d["classes"]) and "pass_rate" in d["results"] and "leave_by_kind" in d["staff"]
    pdf = await app_client.get("/api/v1/reports/monthly/pdf", params={"month": month})
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF" and pdf.headers["content-type"] == "application/pdf"
    assert (await app_client.get("/api/v1/reports/monthly", params={"month": "2999-01"})).status_code == 422
    assert (await app_client.get("/api/v1/reports/monthly")).status_code == 200              # last month by default
    for page in ("/dashboard/admin/monthly", "/dashboard/principal/monthly", "/dashboard/admin/certificates"):
        assert (await app_client.get(page, headers={"accept": "text/html"})).status_code == 200, page

    await _login(app_client, username, PW)
    assert (await app_client.get("/api/v1/reports/monthly")).status_code == 403

    from app.core import clock
    from app.reports import monthly

    monkeypatch.setattr(clock, "institute_now", lambda: datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(monthly, "institute_today", lambda: datetime(2026, 11, 1).date())
    sent = await monthly.send_on_the_first(db_session)
    await db_session.commit()
    assert sent >= 1
    notes = (await db_session.execute(select(Notification).where(Notification.title == "Monthly report for October 2026 is ready"))).scalars().all()
    assert len(notes) == sent and notes[0].link == "/dashboard/principal/monthly"
    assert await monthly.send_on_the_first(db_session) == 0                                    # once a month
    monkeypatch.setattr(clock, "institute_now", lambda: datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc))
    assert await monthly.send_on_the_first(db_session) == 0                                    # only on the 1st
    assert (await db_session.execute(select(func.count()).select_from(Notification).where(Notification.title.like("Monthly report for%")))).scalar_one() == sent
