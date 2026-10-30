"""Email settings and delivery, background-service status, extra care on bank changes, profile reminders,
parents' own email, and library overdue reminders."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.notifications.email_service as email_service
from app.core.config import settings
from app.core.secretbox import open_, seal
from app.notifications.email_models import EmailOutbox, EmailSettings
from tests.integration.test_fees import _create_and_activate_student, _login, _login_admin
from tests.integration.test_staff_role import _create_staff

pytestmark = pytest.mark.asyncio
EM = "/api/v1/settings/email"
SENT: list = []


class FakeSMTP:
    """Stands in for smtplib.SMTP: records what would be sent; a username 'bad' is refused like a real server does."""

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        import smtplib

        if user == "bad":
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    def send_message(self, msg):
        SENT.append({"to": msg["To"], "subject": msg["Subject"], "from": msg["From"], "text": msg.get_body(("plain",)).get_content()})

    def quit(self):
        pass


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(email_service.smtplib, "SMTP_SSL", FakeSMTP)
    email_service.forget_cached_config()
    SENT.clear()
    yield
    email_service.forget_cached_config()


async def _turn_on(client, **over):
    body = {"enabled": True, "host": "smtp.example.com", "port": 587, "security": "starttls", "username": "office@vist.test",
            "password": "app-password-123", "from_name": "VIST Office", "from_email": "office@vist.test", "notify_by_email": True, **over}
    r = await client.put(EM, json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_the_secret_box_round_trips_and_rejects_tampering():
    sealed = seal("p@ss wörd")
    assert sealed.startswith("v1:") and "p@ss" not in sealed
    assert open_(sealed) == "p@ss wörd"
    assert open_(sealed[:-4] + ("AAAA" if not sealed.endswith("AAAA") else "BBBB")) is None
    assert open_(None) is None


async def test_settings_save_the_password_encrypted_and_never_send_it_back(app_client, admin_user, db_session):
    await _login_admin(app_client)
    out = await _turn_on(app_client)
    assert out["has_password"] is True and "password" not in out and out["enabled"] is True
    row = await db_session.get(EmailSettings, 1)
    await db_session.refresh(row)
    assert row.password_enc and "app-password-123" not in row.password_enc and open_(row.password_enc) == "app-password-123"
    kept = await _turn_on(app_client, password=None)                    # saving without a password keeps the saved one
    assert kept["has_password"] is True
    bad = await app_client.put(EM, json={"enabled": True, "host": "", "from_email": "x"})
    assert bad.status_code == 422


async def test_a_test_email_reports_the_servers_answer(app_client, admin_user):
    await _login_admin(app_client)
    await _turn_on(app_client)
    ok = (await app_client.post(f"{EM}/test", json={"to": "me@example.com"})).json()
    assert ok["ok"] is True and SENT[-1]["to"] == "me@example.com" and "Test email" in SENT[-1]["subject"]
    await _turn_on(app_client, username="bad")
    no = (await app_client.post(f"{EM}/test", json={"to": "me@example.com"})).json()
    assert no["ok"] is False and "App Password" in no["error"]
    assert (await app_client.get(EM)).json()["last_test_ok"] is False


async def test_every_notification_is_also_emailed_unless_the_person_switches_it_off(app_client, admin_user, db_session, _engine):
    from app.auth.models import User
    from app.notifications.models import NotificationCategory
    from app.notifications.service import create_notification

    from app.students.models import Student

    sid, username = await _create_and_activate_student(app_client, cnic="82001-0000001-1")
    await _login_admin(app_client)
    user = (await db_session.execute(select(User).where(User.username == username))).scalar_one()
    student = await db_session.get(Student, __import__("uuid").UUID(sid))
    student.email = "ali@example.com"
    await db_session.commit()
    await create_notification(db_session, user_id=user.id, category=NotificationCategory.FEE, title="Before email was on", message="x")
    assert (await db_session.execute(select(EmailOutbox))).scalars().all() == []         # email off: nothing queued
    await _turn_on(app_client)
    await create_notification(db_session, user_id=user.id, category=NotificationCategory.RESULT, title="Results published",
                              message="Your results are out.", link="/dashboard/student/results")
    queued = (await db_session.execute(select(EmailOutbox).where(EmailOutbox.status == "PENDING"))).scalars().all()
    assert len(queued) == 1 and queued[0].to_email == "ali@example.com" and queued[0].subject.startswith("Results published")
    result = await email_service.send_pending(db_session)
    assert result["sent"] == 1 and SENT[-1]["to"] == "ali@example.com" and "Your results are out." in SENT[-1]["text"]

    await _login(app_client, username, "StudentPass123!")
    mine = (await app_client.get("/api/v1/me/email-notifications")).json()
    assert mine["email_notifications"] is True and mine["address"] == "ali@example.com" and mine["institute_sends_email"] is True
    off = await app_client.put("/api/v1/me/email-notifications", json={"email_notifications": False})
    assert off.status_code == 200 and off.json()["email_notifications"] is False
    assert (await app_client.put("/api/v1/me/email-notifications", json={"email": "other@example.com"})).status_code == 422   # students ask the office
    uid = (await db_session.execute(select(User.id).where(User.username == username))).scalar_one()
    db_session.expire_all()
    await create_notification(db_session, user_id=uid, category=NotificationCategory.RESULT, title="Second", message="y")
    assert len((await db_session.execute(select(EmailOutbox).where(EmailOutbox.status == "PENDING"))).scalars().all()) == 0


async def test_a_failing_mail_server_is_retried_then_marked_failed(app_client, admin_user, db_session):
    from datetime import datetime, timezone

    await _login_admin(app_client)
    await _turn_on(app_client, username="bad")
    db_session.add(EmailOutbox(to_email="x@example.com", subject="s", body_text="b"))
    await db_session.flush()
    for _ in range(len(email_service.RETRY_MINUTES) + 1):
        for r in (await db_session.execute(select(EmailOutbox))).scalars():
            r.next_try_at = datetime.now(timezone.utc)
        await email_service.send_pending(db_session)
    row = (await db_session.execute(select(EmailOutbox))).scalar_one()
    assert row.status == "FAILED" and row.attempts == len(email_service.RETRY_MINUTES) + 1 and "App Password" in row.last_error
    st = (await app_client.get("/api/v1/system/status")).json()
    assert st["email"]["failed_24h"] == 1 and any("emails failed" in p for p in st["problems"])
    retry = (await app_client.post(f"{EM}/outbox/retry")).json()
    assert retry["queued_again"] == 1


async def test_the_status_shows_when_the_sender_and_worker_are_not_running(app_client, admin_user, db_session):
    from app.core.background import beat

    await _login_admin(app_client)
    st = (await app_client.get("/api/v1/system/status")).json()
    assert st["sender"]["running"] is False and any("not running" in p for p in st["problems"])
    await beat(db_session, "sender", "test")
    await db_session.commit()
    st = (await app_client.get("/api/v1/system/status")).json()
    assert st["sender"]["running"] is True


async def test_a_bank_change_needs_confirmation_and_tells_payroll(app_client, admin_user, db_session):
    from app.notifications.models import Notification

    emp_id, username = await _create_staff(app_client, db_session, cnic="82001-0000002-2")
    r = await app_client.post("/api/v1/profile-changes/me", json={"values": {"bank_name": "Meezan Bank", "bank_iban": "PK36MEZN0001234567890123"}})
    assert r.status_code == 200
    req = (await app_client.get("/api/v1/profile-changes/me")).json()["pending"]
    assert req["bank_change"] is True
    await _login_admin(app_client)
    plain = await app_client.post(f"/api/v1/profile-changes/{req['id']}/approve")
    assert plain.status_code == 422 and "bank account" in plain.text
    ok = await app_client.post(f"/api/v1/profile-changes/{req['id']}/approve", json={"confirm_bank": True})
    assert ok.status_code == 200, ok.text
    titles = [n.title for n in (await db_session.execute(select(Notification))).scalars()]
    assert any(t == "Your salary bank account was changed" for t in titles)


async def test_reminders_list_what_is_missing_and_do_not_nag(app_client, admin_user, db_session):
    from app.notifications.models import Notification

    sid, username = await _create_and_activate_student(app_client, cnic="82001-0000003-3")
    await _login_admin(app_client)
    rows = (await app_client.get("/api/v1/records/completeness/incomplete", params={"type": "students"})).json()
    me = next(x for x in rows if x["id"] == sid)
    assert me["percent"] < 100 and "Photo" in me["missing"]
    first = (await app_client.post("/api/v1/records/completeness/remind", json={"type": "students", "ids": [sid]})).json()
    assert first["sent"] == 1
    again = (await app_client.post("/api/v1/records/completeness/remind", json={"type": "students", "ids": [sid]})).json()
    assert again["sent"] == 0 and again["skipped_recent"] == 1
    note = (await db_session.execute(select(Notification).where(Notification.title == "Please complete your profile"))).scalars().one()
    assert "Still missing:" in note.message and "Photo" in note.message


async def test_parents_set_their_own_email(app_client, admin_user, db_session):
    from app.auth.models import Role, User, UserRole
    from app.auth.permissions import Roles
    from app.core.security import hash_password

    role = (await db_session.execute(select(Role).where(Role.code == Roles.PARENT))).scalar_one()
    u = User(username="parent_one", password_hash=hash_password("ParentPass123!x"), must_change_password=False)
    db_session.add(u)
    await db_session.flush()
    db_session.add(UserRole(user_id=u.id, role_id=role.id))
    await db_session.commit()
    await _login(app_client, "parent_one", "ParentPass123!x")
    assert (await app_client.put("/api/v1/me/email-notifications", json={"email": "not-an-email"})).status_code == 422
    r = await app_client.put("/api/v1/me/email-notifications", json={"email": "Guardian@Example.com"})
    assert r.status_code == 200 and r.json()["address"] == "guardian@example.com"
    assert (await app_client.get("/dashboard/parent/account")).status_code == 200


async def test_overdue_library_books_list_late_fees_and_remind_once_a_day(app_client, admin_user, db_session):
    from datetime import timedelta

    from app.core.clock import institute_today
    from app.library.models import BookIssue

    sid, username = await _create_and_activate_student(app_client, cnic="82001-0000004-4")
    await _login_admin(app_client)
    book = await app_client.post("/api/v1/library/books", json={"title": "Anatomy", "author": "Gray", "initial_copies": 1})
    assert book.status_code == 201, book.text
    copies = (await app_client.get(f"/api/v1/library/books/{book.json()['id']}/copies")).json()
    issued = await app_client.post("/api/v1/library/issues", json={"book_copy_id": copies[0]["id"], "borrower_type": "STUDENT", "borrower_id": sid,
                                                                    "due_date": str(institute_today() + timedelta(days=7))})
    assert issued.status_code == 201, issued.text
    loan = await db_session.get(BookIssue, __import__("uuid").UUID(issued.json()["id"]))
    loan.due_date = institute_today() - timedelta(days=5)                    # make it overdue
    await db_session.commit()
    d = (await app_client.get("/api/v1/library/issues/overdue/detailed")).json()
    item = next(i for i in d["items"] if i["id"] == issued.json()["id"])
    assert item["days_overdue"] == 5 and item["late_fee"] == min(5 * settings.library_late_fee_per_day, settings.library_late_fee_max or 1e9)
    assert item["has_login"] is True
    r1 = (await app_client.post("/api/v1/library/issues/overdue/remind", json={"issue_ids": [item["id"]]})).json()
    r2 = (await app_client.post("/api/v1/library/issues/overdue/remind", json={"issue_ids": [item["id"]]})).json()
    assert r1["sent"] == 1 and r2["sent"] == 0 and r2["skipped_recent"] == 1