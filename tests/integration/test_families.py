"""Batch 1 for families: Urdu, fee reminders before the due date, and the Monday note to parents."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from app.auth.models import User
from app.core.clock import institute_today
from app.core.config import settings
from app.notifications import family as fam
from app.notifications.models import Notification
from app.sms.models import SmsMessage
from tests.integration.test_parent_portal import P, _create_active_fee_structure, _login_admin, _parent_login

pytestmark = pytest.mark.asyncio
PW = "StudentPass123!"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(settings, "sms_to_guardian", 1)


def _at(monkeypatch, *, weekday: int | None = None, hour: int = 10):
    from app.core import clock

    d = institute_today()
    if weekday is not None:
        d = d + timedelta(days=(weekday - d.weekday()) % 7)
    monkeypatch.setattr(clock, "institute_now", lambda: datetime.combine(d, datetime.min.time()).replace(hour=hour, tzinfo=timezone.utc))
    return d


# ------------------------------------------------------------------ Urdu
def test_the_urdu_dictionary_is_complete_and_consistent():
    d = json.loads(Path("static/i18n/ur.json").read_text(encoding="utf-8"))
    assert len(d) > 500
    for k, v in d.items():
        assert v.strip() and k.count("{n}") == v.count("{n}"), k
        assert re.search(r"[؀-ۿ]", v) or k in ("CGPA",), k          # every value is really Urdu
    for must in ("Sign in", "My Fees", "Download challan", "Attendance", "Marks", "Notices", "How to pay"):
        assert must in d, must


async def test_language_is_kept_on_the_account_and_switches_the_pages(app_client, admin_user, db_session):
    from tests.integration.test_attendance_and_grading import _login, _setup_offering_with_enrolled_students

    _off, studs = await _setup_offering_with_enrolled_students(app_client, code="UR1", n_students=1, cnic_base=8100)
    sid, username, _ = studs[0]
    app_client.cookies.clear()
    page = await app_client.get("/", headers={"accept": "text/html"})
    assert '<html lang="en" dir="ltr">' in page.text and "/static/js/i18n.js" in page.text
    assert 'data-lang-set="en" lang="en" aria-pressed="true"' in page.text and 'data-lang-set="ur" lang="ur" aria-pressed="false"' in page.text

    await _login(app_client, username, PW)
    r = await app_client.post("/api/v1/auth/me/language", json={"language": "ur"})
    assert r.status_code == 200 and r.json() == {"language": "ur"} and r.cookies.get("vist_lang") == "ur"
    assert (await app_client.post("/api/v1/auth/me/language", json={"language": "fr"})).status_code == 422
    u = (await db_session.execute(select(User).where(User.username == username))).scalar_one()
    await db_session.refresh(u)
    assert u.language == "ur"
    home = await app_client.get("/dashboard/student", headers={"accept": "text/html"})
    assert '<html lang="ur" dir="rtl">' in home.text and ">English<" in home.text

    app_client.cookies.clear()                                                   # a new phone: the account decides
    again = await app_client.post("/api/v1/auth/login", json={"username": username, "password": PW})
    assert again.cookies.get("vist_lang") == "ur"
    assert (await app_client.get("/api/v1/auth/me/language")).json() == {"language": "ur"}


# ------------------------------------------------------------------ fee reminders
async def _student_with_fee(app_client, db_session, *, cnic: str, due_in: int, link_parent: bool = True):
    from tests.integration.test_fees import _create_and_activate_student
    from app.fees.models import FeeLedgerEntry
    from app.fees.payment_models import PaymentChallan

    sid, _ = await _create_and_activate_student(app_client, cnic=cnic)
    await _create_active_fee_structure(app_client, total_amount=12000, installment_count=1)
    await _login_admin(app_client)
    parent = (await app_client.post(f"{P}/students/{sid}")).json() if link_parent else None
    ch = (await db_session.execute(select(PaymentChallan).order_by(PaymentChallan.generated_at.desc()))).scalars().first()
    due = institute_today() + timedelta(days=due_in)
    await db_session.execute(update(PaymentChallan).where(PaymentChallan.id == ch.id).values(due_date=due))
    await db_session.execute(update(FeeLedgerEntry).where(FeeLedgerEntry.id == ch.fee_ledger_entry_id).values(due_date=due))
    await db_session.commit()
    return sid, parent, ch.consumer_number


async def test_fee_reminders_go_7_and_3_days_before_and_on_the_day_in_each_persons_language(app_client, admin_user, db_session, monkeypatch):
    sid, parent, consumer = await _student_with_fee(app_client, db_session, cnic="81001-0000001-1", due_in=3)
    parent_user = (await db_session.execute(select(User).where(User.username == parent["username"]))).scalar_one()
    parent_user.language = "ur"
    await db_session.commit()
    _at(monkeypatch)

    assert await fam.fee_reminders(db_session) == 1
    await db_session.commit()
    notes = (await db_session.execute(select(Notification).where(Notification.title.in_(["Fee reminder", "فیس کی یاد دہانی"])))).scalars().all()
    by_title = {n.title: n for n in notes}
    assert "Fee reminder" in by_title and "فیس کی یاد دہانی" in by_title                       # student in English, parent in Urdu
    assert "Rs. 12,000" in by_title["Fee reminder"].message and consumer in by_title["Fee reminder"].message
    assert "3 days" in by_title["Fee reminder"].message and by_title["فیس کی یاد دہانی"].link == "/dashboard/parent/fees"
    sms = (await db_session.execute(select(SmsMessage).where(SmsMessage.event == "FEE_REMINDER"))).scalars().all()
    assert {m.student_id for m in sms} == {uuid.UUID(sid)}
    assert await fam.fee_reminders(db_session) == 0                                            # once a day

    # paid challans are never reminded
    from app.fees.payment_models import ChallanStatus, PaymentChallan
    await db_session.execute(update(PaymentChallan).values(status=ChallanStatus.PAID))
    await db_session.commit()
    from app.notifications.email_models import ServiceHeartbeat
    await db_session.execute(ServiceHeartbeat.__table__.delete())
    await db_session.commit()
    assert await fam.fee_reminders(db_session) == 0


def test_reminder_wording_in_both_languages():
    from datetime import date

    t, m = fam.fee_text("en", who="Ali", amount=5000, fee="Tuition", due=date(2026, 11, 20), days=0, consumer="123", for_parent=True)
    assert t == "Last day to pay the fee" and m.startswith("Ali's Tuition fee of Rs. 5,000 is due today")
    t, m = fam.fee_text("ur", who="علی", amount=5000, fee="Tuition", due=date(2026, 11, 20), days=7, consumer="123", for_parent=False)
    assert t == "فیس کی یاد دہانی" and "7 دن بعد" in m and "کنزیومر نمبر: 123" in m


# ------------------------------------------------------------------ Monday note to parents
async def test_parents_get_a_monday_note_only_when_something_changed(app_client, admin_user, db_session, monkeypatch):
    from app.academics.models import Enrollment
    from app.assignments.models import Assignment
    from tests.integration.test_attendance_and_grading import _setup_offering_with_enrolled_students

    off, studs = await _setup_offering_with_enrolled_students(app_client, code="MN1", n_students=1, cnic_base=8200)
    sid = studs[0][0]
    await _login_admin(app_client)
    made = (await app_client.post(f"{P}/students/{sid}")).json()
    from app.students.models import StudentGuardian
    await db_session.execute(update(StudentGuardian).where(StudentGuardian.student_id == uuid.UUID(sid)).values(contact_no="0300-1234567"))
    now = datetime.now(timezone.utc)
    await db_session.execute(update(Enrollment).where(Enrollment.course_offering_id == uuid.UUID(off)).values(enrolled_at=now - timedelta(days=30)))
    from app.academics.models import CourseOffering
    await db_session.execute(update(CourseOffering).where(CourseOffering.id == uuid.UUID(off)).values(term_end_date=institute_today() + timedelta(days=40)))
    for t in ("Essay", "Lab report"):
        db_session.add(Assignment(course_offering_id=uuid.UUID(off), title=t, instructions="x" * 20, deadline=now - timedelta(days=1)))
    await db_session.commit()

    _at(monkeypatch, weekday=1)                                                                 # Tuesday: nothing
    assert await fam.parent_weekly(db_session) == 0
    _at(monkeypatch, weekday=0)                                                                 # Monday
    assert await fam.parent_weekly(db_session) == 1
    await db_session.commit()
    note = (await db_session.execute(select(Notification).where(Notification.title.like("%needs attention")))).scalars().one()
    assert "MN1-C1 (2 assignments missed)" in note.message and note.link == "/dashboard/parent"
    sms = (await db_session.execute(select(SmsMessage).where(SmsMessage.event == "PARENT_WEEKLY"))).scalars().all()
    assert len(sms) == 1 and sms[0].recipient == "GUARDIAN"

    from app.notifications.email_models import ServiceHeartbeat
    await db_session.execute(ServiceHeartbeat.__table__.delete())                              # next Monday, nothing changed
    await db_session.commit()
    assert await fam.parent_weekly(db_session) == 0
    assert (await db_session.execute(select(func.count()).select_from(Notification).where(Notification.title.like("%needs attention")))).scalar_one() == 1

    # the parent home page shows it too
    await _parent_login(app_client, made["username"], made["temporary_password"])
    detail = (await app_client.get(f"{P}/me/children/{sid}")).json()
    assert detail["attention"] == [{"course_code": "MN1-C1", "course_title": detail["attention"][0]["course_title"], "kind": "assignments", "value": 2}]


def test_attention_wording_in_both_languages():
    items = [{"course_code": "PHY-101", "kind": "attendance", "value": 68}, {"course_code": "MTH-101", "kind": "assignments", "value": 2}]
    t, m = fam.attention_text("en", "Ali", items)
    assert t == "Ali needs attention" and m == "Ali needs attention in: PHY-101 (attendance 68%); MTH-101 (2 assignments missed). See the parent portal for details."
    t, m = fam.attention_text("ur", "Ali", items)
    assert "توجہ" in t and "PHY-101 (حاضری 68%)" in m


async def test_the_language_picked_on_the_sign_in_page_is_kept_on_the_account(app_client, admin_user, db_session):
    from tests.integration.test_attendance_and_grading import _setup_offering_with_enrolled_students

    _off, studs = await _setup_offering_with_enrolled_students(app_client, code="UR2", n_students=1, cnic_base=8150)
    username = studs[0][1]
    app_client.cookies.clear()
    app_client.cookies.set("vist_lang", "ur")
    r = await app_client.post("/api/v1/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200 and r.cookies.get("vist_lang") == "ur"
    u = (await db_session.execute(select(User).where(User.username == username))).scalar_one()
    await db_session.refresh(u)
    assert u.language == "ur"
