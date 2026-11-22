"""Family home cards and phone notices (app/family): a parent's card per child (fees with Pay now), a student's Today
card (classes and attendance), and notices for an absence in class and a published result -- each told once."""
from __future__ import annotations

import uuid
from datetime import datetime, time, timezone

import pytest
from sqlalchemy import select

from app.academics.models import ClassSession, ClassSessionStatus
from app.attendance.models import AttendanceRecord, AttendanceStatus
from app.core.clock import institute_today
from app.core.config import settings
from app.family.service import absence_round, result_notices
from app.notifications.models import Notification
from app.students.models import Student
from tests.integration.test_attendance_and_grading import _setup_offering_with_enrolled_students
from tests.integration.test_fees import _create_active_fee_structure, _create_and_activate_student, _login, _login_admin
from tests.integration.test_parent_portal import _parent_login

pytestmark = pytest.mark.asyncio
P = "/api/v1/parents"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def test_parent_sees_a_card_per_child_with_pay_now(app_client, admin_user):
    a, _ua = await _create_and_activate_student(app_client, cnic="76101-0000001-1")
    b, _ub = await _create_and_activate_student(app_client, cnic="76101-0000002-2")          # same guardian -> sibling
    await _create_active_fee_structure(app_client, total_amount=6000, installment_count=1)
    await _login_admin(app_client)
    made = (await app_client.post(f"{P}/students/{a}")).json()
    await app_client.post(f"{P}/students/{a}", json={"sibling_ids": [b]})
    await _parent_login(app_client, made["username"], made["temporary_password"])
    cards = (await app_client.get("/api/v1/family/cards")).json()
    assert {c["id"] for c in cards} == {a, b}
    c = cards[0]
    assert set(c) >= {"today", "fees", "next_exam", "latest_result"} and c["fees"]["to_pay"] == 6000.0
    assert c["fees"]["pay"] and c["fees"]["pay"]["amount"] == 6000.0
    assert (await app_client.get(f"/api/v1/fees/challans/{c['fees']['pay']['challan_id']}/pdf")).status_code == 200
    page = (await app_client.get("/dashboard/parent")).text
    assert 'id="family-cards"' in page and "family-cards.js" in page


async def test_absence_and_result_notices_reach_student_and_parent(app_client, admin_user, db_session):
    offering, students = await _setup_offering_with_enrolled_students(app_client, code="FAM1", n_students=1, cnic_base=7700)
    sid, username, _enr = students[0]
    await _login_admin(app_client)
    made = (await app_client.post(f"{P}/students/{sid}")).json()
    st = await db_session.get(Student, uuid.UUID(sid))
    cs = ClassSession(course_offering_id=uuid.UUID(offering), session_date=institute_today(), start_time=time(9), end_time=time(10), room="R-1",
                      status=ClassSessionStatus.SCHEDULED)
    db_session.add(cs)
    await db_session.flush()
    db_session.add(AttendanceRecord(class_session_id=cs.id, student_id=st.id, status=AttendanceStatus.ABSENT, marked_at=datetime.now(timezone.utc)))
    await db_session.commit()
    student_user = st.user_id

    n = await absence_round(db_session)
    assert n == 2                                                                   # the student and the parent
    titles = [x.title for x in (await db_session.execute(select(Notification).where(Notification.title.like("%absent%FAM1%")))).scalars()]
    assert "Marked absent: FAM1-C1" in titles and any(t.endswith("was absent: FAM1-C1") for t in titles)
    assert await absence_round(db_session) == 0                                     # told once
    assert await result_notices(db_session, uuid.UUID(offering), course_code="FAM1", course_title="Family test", internal_only=False) == 2
    assert (await db_session.execute(select(Notification).where(Notification.user_id == student_user, Notification.title == "Result published: FAM1"))).scalar_one()

    # the student's Today card shows the class and the mark
    await _login(app_client, username, "StudentPass123!")
    me = (await app_client.get("/api/v1/family/cards")).json()
    assert len(me) == 1 and me[0]["today"]["absent"] == 1 and me[0]["today"]["classes"][0]["course_code"] == "FAM1-C1"
    page = (await app_client.get("/dashboard/student")).text
    assert 'id="family-cards"' in page
    # switched off: nothing is sent
    settings_off = settings.notify_family_absence
    try:
        settings.notify_family_absence = 0
        assert await absence_round(db_session) == 0
    finally:
        settings.notify_family_absence = settings_off
    assert made["username"]
