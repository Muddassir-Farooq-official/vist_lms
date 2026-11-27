"""Guides (1.69.0): the administrator's "Set up your LMS" checklist, each person's "To do" (profile still to complete,
with a 7-day grace period; a teacher's classes without attendance), and the manual's words for each page's tour."""
from __future__ import annotations

import uuid
from datetime import time, timedelta

import pytest
from sqlalchemy import select

from app.academics.models import ClassSession
from app.auth.models import User
from app.core.clock import institute_today
from app.students.models import Student
from tests.integration.test_attendance_and_grading import _create_and_activate_student, _login, _login_admin
from tests.integration.test_smart_timetable import _q as _q  # noqa: F401  (rate limits off)

pytestmark = pytest.mark.asyncio
O = "/api/v1/onboarding"


async def test_the_setup_checklist_ticks_itself_and_is_for_the_administrator(app_client, admin_user, db_session):
    await _login_admin(app_client)
    d = (await app_client.get(f"{O}/setup")).json()
    keys = [s["key"] for s in d["steps"]]
    assert keys[:3] == ["institute", "programs", "terms"] and "timetable" in keys and d["total"] == len(keys)
    programs = next(s for s in d["steps"] if s["key"] == "programs")
    assert programs["done"] is False and programs["link"] == "/dashboard/admin/programs"
    assert (await app_client.post("/api/v1/programs", json={"code": "OBN", "name": "Onboarding Nursing", "duration_years": 4, "total_semesters": 8})).status_code == 201
    d = (await app_client.get(f"{O}/setup")).json()
    assert next(s for s in d["steps"] if s["key"] == "programs")["done"] is True

    _sid, username = await _create_and_activate_student(app_client, cnic="35202-7800001-1")
    await _login(app_client, username, "StudentPass123!")
    assert (await app_client.get(f"{O}/setup")).status_code == 403


async def test_a_student_is_reminded_to_complete_their_profile_then_it_opens_first(app_client, admin_user, db_session):
    sid, username = await _create_and_activate_student(app_client, cnic="35202-7800002-2")
    await _login(app_client, username, "StudentPass123!")
    d = (await app_client.get(f"{O}/me")).json()
    p = d["profile"]
    assert p and p["missing"] and "Photo" in p["missing"] and p["link"] == "/dashboard/my-profile"
    assert d["grace_days_left"] == 7 and d["must_finish"] is False
    assert any(x["key"] == "push" for x in d["todo"])                 # phone notifications not on yet

    # a change sent to the office counts as done by them
    r = await app_client.post("/api/v1/profile-changes/me", json={"values": {"blood_group": "B+"}, "note": "my blood group"})
    assert r.status_code in (200, 201), r.text
    p2 = (await app_client.get(f"{O}/me")).json()["profile"]
    bg = next(x for x in p2["items"] if x["key"] == "blood_group")
    assert bg["done"] and bg["waiting"]

    # eight days on, still incomplete: the profile opens first
    student = await db_session.get(Student, uuid.UUID(sid))
    user = await db_session.get(User, student.user_id)
    user.nudge_started_on = institute_today() - timedelta(days=8)
    await db_session.commit()
    d = (await app_client.get(f"{O}/me")).json()
    assert d["must_finish"] is True and d["grace_days_left"] == 0


async def test_a_teacher_is_told_about_todays_classes_without_attendance(app_client, admin_user, db_session):
    from tests.integration.test_announcements import _first_login
    from tests.integration.test_smart_timetable import A, _setup

    offs, _t, logins, periods = await _setup(app_client, db_session)
    today = institute_today()
    day = today.strftime("%A").upper()
    r = await app_client.post(f"{A}/offerings/{offs['101A']}/timetable-slots", json={"day_of_week": day, "period_id": periods[0], "room": "SR1"})
    if r.status_code != 201:
        pytest.skip("no class can be placed today: " + r.text[:100])
    cs = (await db_session.execute(select(ClassSession).where(ClassSession.course_offering_id == offs["101A"], ClassSession.session_date == today))).scalar_one()
    cs.start_time, cs.end_time = time(0, 1), time(0, 2)                # started already, whatever the time the test runs
    await db_session.commit()
    username, first = logins["STA"]
    await _first_login(app_client, username, first)
    todo = (await app_client.get(f"{O}/me")).json()["todo"]
    att = [x for x in todo if x["key"] == "attendance"]
    assert att and att[0]["count"] == 1 and "SM101" in att[0]["text"] and att[0]["link"] == "/dashboard/faculty/attendance"


async def test_tours_use_the_manuals_words_and_load_on_every_page(app_client, admin_user):
    await _login_admin(app_client)
    h = (await app_client.get(f"{O}/page-help", params={"portal": "admin", "label": "Employees"})).json()
    assert h["title"] == "Employees" and "staff" in h["text"].lower()
    h = (await app_client.get(f"{O}/page-help", params={"portal": "admin", "label": "Payroll"})).json()
    assert h["text"]
    assert (await app_client.get(f"{O}/page-help", params={"portal": "admin", "label": "No such page"})).json() == {}
    common = (await app_client.get("/static/js/admin-common.js")).text
    assert "/static/js/guide.js" in common
    for f in ("guide.js", "tours.js", "tour.js"):
        assert (await app_client.get(f"/static/js/{f}")).status_code == 200
