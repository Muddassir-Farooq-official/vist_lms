"""Every account must confirm an email address with a one-time code before using the portal (when the administrator turns
it on), and administration email addresses get a copy of notifications (one email for a notice sent to many people)."""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

import app.notifications.email_service as email_service
from app.auth.models import User
from app.core.config import settings
from app.notifications.email_models import EmailOutbox, EmailSettings, EmailVerificationCode
from tests.integration.test_email_and_services import SENT, FakeSMTP, _turn_on
from tests.integration.test_fees import _login, _login_admin
from tests.integration.test_staff_role import _create_staff

pytestmark = pytest.mark.asyncio
EM = "/api/v1/settings/email"


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(settings, "session_check_seconds", 0)
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(email_service.smtplib, "SMTP_SSL", FakeSMTP)
    email_service.forget_cached_config()
    SENT.clear()
    yield
    email_service.forget_cached_config()


async def _verify_admin_directly(db, email="head@vist.test"):
    admin = (await db.execute(select(User).where(User.username == "admin_test"))).scalar_one()
    admin.email, admin.email_verified_at = email, datetime.now(timezone.utc)
    await db.commit()


async def _require_on(client, db):
    """Email on, a test that arrived, the administrator's own email verified, then require verification."""
    await _login_admin(client)
    await _turn_on(client)
    assert (await client.post(f"{EM}/test", json={"to": "head@vist.test"})).json()["ok"] is True
    await _verify_admin_directly(db)
    r = await _turn_on(client, require_verified_email=True)
    assert r["require_verified_email"] is True
    return r


def _last_code() -> str:
    return re.search(r"\b(\d{6})\b", SENT[-1]["subject"]).group(1)


async def test_turning_it_on_is_refused_until_email_works_and_your_own_email_is_verified(app_client, admin_user, db_session):
    await _login_admin(app_client)
    off = await app_client.put(EM, json={"enabled": False, "require_verified_email": True})
    assert off.status_code == 422 and "Turn email on" in off.text
    await _turn_on(app_client)
    untested = await app_client.put(EM, json={"enabled": True, "host": "smtp.example.com", "from_email": "office@vist.test", "require_verified_email": True})
    assert untested.status_code == 422 and "test email" in untested.text
    assert (await app_client.post(f"{EM}/test", json={"to": "head@vist.test"})).json()["ok"] is True
    unverified = await app_client.put(EM, json={"enabled": True, "host": "smtp.example.com", "from_email": "office@vist.test", "require_verified_email": True})
    assert unverified.status_code == 422 and "Verify your own email first" in unverified.text
    # Nothing is required yet, so a fresh account works normally.
    emp_id, username = await _create_staff(app_client, db_session, cnic="84001-0000001-1")
    assert (await app_client.get("/api/v1/employees/me")).status_code == 200
    assert (await app_client.get("/api/v1/auth/email")).json()["required"] is False


async def test_a_person_must_verify_with_a_code_before_using_the_portal(app_client, admin_user, db_session):
    emp_id, username = await _create_staff(app_client, db_session, cnic="84001-0000002-2")
    out = await _require_on(app_client, db_session)
    assert out["verified_accounts"] >= 1 and out["active_accounts"] >= 2
    assert (await app_client.get("/api/v1/employees")).status_code == 200            # the administrator is verified: works

    login = await app_client.post("/api/v1/auth/login", json={"username": username, "password": "StaffReal123!"})
    assert login.status_code == 200 and login.json()["redirect_to"] == "/verify-email" and login.json()["email_verification_required"] is True
    blocked = await app_client.get("/api/v1/employees/me")
    assert blocked.status_code == 403 and "Verify your email" in blocked.text
    me = await app_client.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["email_verification_required"] is True
    page = await app_client.get("/verify-email")
    assert page.status_code == 200 and "verify-email.js" in page.text

    taken = await app_client.post("/api/v1/auth/email/send-code", json={"email": "HEAD@vist.test"})
    assert taken.status_code == 422 and "another account" in taken.text               # the administrator's address
    bad = await app_client.post("/api/v1/auth/email/send-code", json={"email": "not-an-email"})
    assert bad.status_code == 422
    sent = await app_client.post("/api/v1/auth/email/send-code", json={"email": " Staff.Member@Example.com "})
    assert sent.status_code == 200, sent.text
    assert sent.json()["sent_to"] == "staff.member@example.com" and SENT[-1]["to"] == "staff.member@example.com"
    code = _last_code()
    again = await app_client.post("/api/v1/auth/email/send-code", json={"email": "staff.member@example.com"})
    assert again.status_code == 429                                                  # one code a minute
    stored = (await db_session.execute(select(EmailVerificationCode))).scalars().all()
    assert all(code not in r.code_hash for r in stored)                              # only a hash is kept
    otp_rows = (await db_session.execute(select(EmailOutbox).where(EmailOutbox.kind == "OTP"))).scalars().all()
    assert otp_rows and all(code not in (r.body_text + r.subject) for r in otp_rows)

    wrong = await app_client.post("/api/v1/auth/email/verify", json={"code": "000000" if code != "000000" else "111111"})
    assert wrong.status_code == 422 and "4 tries left" in wrong.text
    ok = await app_client.post("/api/v1/auth/email/verify", json={"code": code})
    assert ok.status_code == 200, ok.text
    assert ok.json()["email"] == "staff.member@example.com" and ok.json()["redirect_to"] == "/dashboard/staff"
    assert (await app_client.get("/api/v1/employees/me")).status_code == 200          # in straight away
    status = (await app_client.get("/api/v1/auth/email")).json()
    assert status["verified"] is True and status["required"] is False
    login2 = await app_client.post("/api/v1/auth/login", json={"username": username, "password": "StaffReal123!"})
    assert login2.json()["redirect_to"] == "/dashboard/staff"


async def test_five_wrong_codes_finish_the_code_and_turning_email_off_pauses_the_requirement(app_client, admin_user, db_session):
    emp_id, username = await _create_staff(app_client, db_session, cnic="84001-0000003-3")
    await _require_on(app_client, db_session)
    await _login(app_client, username, "StaffReal123!")
    assert (await app_client.post("/api/v1/auth/email/send-code", json={"email": "third@example.com"})).status_code == 200
    code = _last_code()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(4):
        await app_client.post("/api/v1/auth/email/verify", json={"code": wrong})
    fifth = await app_client.post("/api/v1/auth/email/verify", json={"code": wrong})
    assert "Too many wrong tries" in fifth.text
    late = await app_client.post("/api/v1/auth/email/verify", json={"code": code})
    assert late.status_code == 422 and "Ask for a new code" in late.text            # the right code no longer works
    assert (await app_client.get("/api/v1/employees/me")).status_code == 403

    await _login_admin(app_client)
    await _turn_on(app_client, enabled=False, require_verified_email=True)            # mail server off: nobody could get a code
    await _login(app_client, username, "StaffReal123!")
    assert (await app_client.get("/api/v1/employees/me")).status_code == 200


async def test_the_administration_gets_one_copy_of_a_notice_sent_to_many_people(app_client, admin_user, db_session):
    from app.notifications.models import NotificationCategory
    from app.notifications.service import create_notification

    from app.employees.models import JobRole
    from tests.integration.test_file_download_authorization import _employee_payload

    a_id, a_user = await _create_staff(app_client, db_session, cnic="84001-0000004-4")
    job = JobRole(code="STAFF_V", title="Office Assistant 3", default_role_code="STAFF")
    db_session.add(job)
    await db_session.commit()
    await _login_admin(app_client)
    b = await app_client.post("/api/v1/employees", json=_employee_payload(job.id, cnic="84001-0000005-5"))
    assert b.status_code == 201, b.text
    b_user = b.json()["username"]
    bad = await app_client.put(EM, json={"enabled": True, "host": "smtp.example.com", "from_email": "office@vist.test", "admin_emails": "principal@vist.test\nnope"})
    assert bad.status_code == 422 and "nope" in bad.text
    out = await _turn_on(app_client, admin_emails="Principal@vist.test, office.head@vist.test", admin_copy_categories=["TASK", "FEE"])
    assert out["admin_emails"] == ["principal@vist.test", "office.head@vist.test"] and out["admin_copy_categories"] == ["FEE", "TASK"]

    users = {u.username: u.id for u in (await db_session.execute(select(User))).scalars()}
    for name in (a_user, b_user):
        await create_notification(db_session, user_id=users[name], category=NotificationCategory.TASK, title="Stock count due",
                                  message="Please upload your stock sheet by Friday.")
    await create_notification(db_session, user_id=users[a_user], category=NotificationCategory.LIBRARY, title="Book overdue", message="Return it.")
    await db_session.commit()

    copies = (await db_session.execute(select(EmailOutbox).where(EmailOutbox.kind == "ADMIN_COPY"))).scalars().all()
    assert sorted(c.to_email for c in copies) == ["office.head@vist.test", "principal@vist.test"]   # library is not copied
    for c in copies:
        assert c.subject.startswith("Copy: Stock count due") and "Sent to 2 people" in c.body_text
        assert len(c.copy_recipients.split("\n")) == 2 and c.next_try_at > datetime.now(timezone.utc)   # waits so more can join

    # Once due, the sender delivers them like any other email.
    for c in copies:
        c.next_try_at = datetime.now(timezone.utc)
    await db_session.commit()
    result = await email_service.send_pending(db_session)
    await db_session.commit()
    assert result["sent"] >= 2 and {"principal@vist.test", "office.head@vist.test"} <= {m["to"] for m in SENT}