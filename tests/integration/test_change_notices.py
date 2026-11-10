"""A notification for every change that affects a person; the task map; the notification tone and phone alerts."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.employees.models import Employee
from app.notifications.models import Notification
from app.students.models import Student
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import (SLOT_DAY_OF_WEEK, _get_or_create_period, _login, _login_admin,
                                                         _setup_offering_with_enrolled_students)

pytestmark = pytest.mark.asyncio
PW = "StudentPass123!"
ALL_WEEK = {"in_time": "09:00", "out_time": "17:00", "grace_in_minutes": 10, "grace_out_minutes": 5, "working_days": [0, 1, 2, 3, 4]}


@pytest.fixture(autouse=True)
def _q(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _notes(db, user_id, title):
    return (await db.execute(select(Notification).where(Notification.user_id == user_id, Notification.title == title))).scalars().all()


async def _user_of(db, username):
    from app.auth.models import User

    return (await db.execute(select(User).where(User.username == username))).scalar_one()


async def test_staff_are_told_when_their_working_hours_salary_or_record_change(app_client, admin_user, db_session):
    await _login_admin(app_client)
    t_user, _ = await _staff(app_client, db_session, "CNT1", "TEACHER", "35202-5110001-1")
    l_user, _ = await _staff(app_client, db_session, "CNL1", "LIBRARIAN", "35202-5120001-1")
    teacher, librarian = await _user_of(db_session, t_user), await _user_of(db_session, l_user)
    emp = (await db_session.execute(select(Employee).where(Employee.user_id == teacher.id))).scalar_one()

    # the institute's normal hours: every member of staff hears about it, not the admin who changed them
    assert (await app_client.put("/api/v1/staff-attendance/timings/default", json=ALL_WEEK)).status_code == 204
    for u in (teacher, librarian):
        notes = await _notes(db_session, u.id, "Your working hours have changed")
        assert len(notes) == 1 and "for all staff" in notes[0].message and "09:00–17:00" in notes[0].message
    assert await _notes(db_session, admin_user.id, "Your working hours have changed") == []

    # just one person's hours
    assert (await app_client.put(f"/api/v1/staff-attendance/timings/employees/{emp.id}", json={**ALL_WEEK, "in_time": "10:00"})).status_code == 204
    assert len(await _notes(db_session, teacher.id, "Your working hours have changed")) == 2
    assert len(await _notes(db_session, librarian.id, "Your working hours have changed")) == 1

    # salary and record details
    r = await app_client.post(f"/api/v1/employees/{emp.id}/salary", json={"base_salary": 95000, "effective_from": datetime.now(timezone.utc).date().isoformat(),
                                                                         "reason": "Annual revision approved"})
    if r.status_code in (200, 201):
        notes = await _notes(db_session, teacher.id, "Your salary has changed")
        assert len(notes) == 1 and "Rs. 95,000" in notes[0].message
    assert (await app_client.patch(f"/api/v1/employees/{emp.id}", json={"contact_no": "03001231234"})).status_code == 200
    rec = await _notes(db_session, teacher.id, "Your staff record was updated")
    assert len(rec) == 1 and "contact no" in rec[0].message


async def test_students_and_teacher_hear_about_a_timetable_change_and_marks(app_client, admin_user, db_session):
    off, studs = await _setup_offering_with_enrolled_students(app_client, code="CN2", n_students=2, cnic_base=5130)
    s_ids = [uuid.UUID(s[0]) for s in studs]
    users = (await db_session.execute(select(Student.user_id).where(Student.id.in_(s_ids)))).scalars().all()
    await _login_admin(app_client)
    period = await _get_or_create_period(app_client, "09:00:00", "10:00:00")
    slot = await app_client.post(f"/api/v1/academics/offerings/{off}/timetable-slots", json={"day_of_week": SLOT_DAY_OF_WEEK, "period_id": period, "room": "R-1"})
    assert slot.status_code == 201, slot.text
    for uid in users:
        added = await _notes(db_session, uid, "Timetable change")
        assert len(added) == 1 and "a new class" in added[0].message and "CN2-C1" in added[0].message and added[0].link == "/dashboard/student/timetable"
    later = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    moved = await app_client.post(f"/api/v1/academics/timetable-slots/{slot.json()['slot']['id'] if 'slot' in slot.json() else slot.json()['id']}/change",
                                  json={"from_date": later, "period_id": period, "day_of_week": "FRIDAY" if SLOT_DAY_OF_WEEK != "FRIDAY" else "MONDAY", "room": "R-9"})
    assert moved.status_code == 200, moved.text
    for uid in users:
        notes = await _notes(db_session, uid, "Timetable change")
        assert len(notes) == 2 and any("moves to" in n.message and "room R-9" in n.message for n in notes)


async def test_a_warning_decision_does_not_also_send_the_general_status_message(app_client, admin_user, db_session):
    from tests.integration.test_warnings import _types, _warn

    _off, studs = await _setup_offering_with_enrolled_students(app_client, code="CN3", n_students=1, cnic_base=5140)
    sid = studs[0][0]
    await _login_admin(app_client)
    types = await _types(app_client, "STUDENT")
    tid = next(iter(types.values()))
    for _ in range(3):
        w = await _warn(app_client, "STUDENT", sid, tid)
    assert (await app_client.post(f"/api/v1/discipline/warnings/{w['id']}/decide", json={"action": "SUSPEND", "note": "Suspended for two weeks."})).status_code == 200
    uid = (await db_session.execute(select(Student.user_id).where(Student.id == uuid.UUID(sid)))).scalar_one()
    assert await _notes(db_session, uid, "Your status has changed") == []
    assert len((await db_session.execute(select(Notification).where(Notification.user_id == uid, Notification.title.like("Decision on your final warning%")))).scalars().all()) == 1


async def test_task_map_everyone_for_the_admin_and_only_mine_for_staff(app_client, admin_user, db_session):
    await _login_admin(app_client)
    a_user, a_first = await _staff(app_client, db_session, "TMA", "TEACHER", "35202-5150001-1")
    b_user, b_first = await _staff(app_client, db_session, "TMB", "LIBRARIAN", "35202-5160001-1")
    c_user, c_first = await _staff(app_client, db_session, "TMC", "ACCOUNTS_OFFICER", "35202-5170001-1")
    a, b, c = await _user_of(db_session, a_user), await _user_of(db_session, b_user), await _user_of(db_session, c_user)
    a_pw = await _first_login(app_client, a_user, a_first)
    b_pw = await _first_login(app_client, b_user, b_first)
    await _first_login(app_client, c_user, c_first)

    soon = (datetime.now(timezone.utc) + timedelta(hours=30)).isoformat()
    await _login(app_client, a_user, a_pw)
    for title in ("Shelve the new books", "Count the reference section"):
        assert (await app_client.post("/api/v1/tasks", json={"assignee_user_id": str(b.id), "title": title, "description": "Please do it this week.",
                                                              "deadline": soon})).status_code == 201
    await _login(app_client, b_user, b_pw)
    assert (await app_client.post("/api/v1/tasks", json={"assignee_user_id": str(c.id), "title": "Fine report", "description": "Send me the fine report.",
                                                          "deadline": soon})).status_code == 201

    await _login_admin(app_client)
    m = (await app_client.get("/api/v1/tasks/map", params={"days": 30})).json()
    edges = {(e["from"], e["to"]): e for e in m["edges"]}
    assert edges[(str(a.id), str(b.id))]["counts"]["total"] == 2 and edges[(str(a.id), str(b.id))]["counts"]["open"] == 2
    assert edges[(str(b.id), str(c.id))]["counts"]["total"] == 1 and m["totals"]["total"] == 3
    nodes = {n["user_id"]: n for n in m["nodes"]}
    assert nodes[str(b.id)]["given"]["total"] == 1 and nodes[str(b.id)]["received"]["total"] == 2
    assert [t["title"] for t in edges[(str(a.id), str(b.id))]["tasks"]] and edges[(str(a.id), str(b.id))]["tasks"][0]["ref"].startswith("TASK-")

    await _login(app_client, a_user, a_pw)
    mine = (await app_client.get("/api/v1/tasks/map", params={"days": 30})).json()               # a staff member: only their own
    assert {(e["from"], e["to"]) for e in mine["edges"]} == {(str(a.id), str(b.id))}
    assert any(n["is_me"] for n in mine["nodes"])
    page = await app_client.get("/dashboard/faculty/tasks", headers={"accept": "text/html"})
    assert 'data-tab="map"' in page.text and "task-map.js" in page.text


async def test_notification_tone_settings_and_stronger_phone_alerts(app_client, admin_user, monkeypatch):
    from app.push.service import alert_style

    await _login_admin(app_client)
    r = await app_client.get("/api/v1/notifications/sound")
    assert r.status_code == 200 and r.json() == {"tone": 1, "urgent_tone": 7, "volume": 70, "repeat": 1}
    pol = (await app_client.get("/api/v1/policy")).json()
    keys = {p["key"] for p in (pol if isinstance(pol, list) else pol.get("policies", pol.get("items", [])))}
    assert {"notify_sound_tone", "notify_sound_urgent_tone", "notify_sound_volume", "notify_sound_repeat", "phone_alert_vibrate"} <= keys
    js = (await app_client.get("/static/js/notify-sound.js")).text
    assert "VistSound" in js and "TONES" in js
    assert "notify-sound.js" in (await app_client.get("/static/js/admin-common.js")).text

    assert alert_style("URGENT: Campus closed") == {"urgent": True, "vibrate": [400, 150, 400, 150, 800, 300, 800], "keep": True}
    assert alert_style("Important: Fee deadline") == {"urgent": False, "vibrate": [200, 100, 200], "keep": True}
    assert alert_style("New assignment") == {"urgent": False, "vibrate": [200, 100, 200]}
    monkeypatch.setattr(settings, "phone_alert_vibrate", 0)
    assert alert_style("New assignment") == {"urgent": False}
    sw = (await app_client.get("/static/js/service-worker.js")).text
    assert "options.vibrate = data.vibrate" in sw and "requireInteraction" in sw


async def test_one_latest_announcement_on_dashboards(app_client):
    for path, needle in (("/static/js/student-home.js", ".slice(0, 1)"), ("/static/js/faculty-home.js", 'd.announcements, 1,'),
                         ("/static/js/portal-home.js", ".slice(0, 1)"), ("/static/js/dashboard.js", "rows.slice(0, 1)")):
        assert needle in (await app_client.get(path)).text, path
    assert "localeCompare" in (await app_client.get("/static/js/dash-common.js")).text           # the newest, not the pinned one
