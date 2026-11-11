"""School mode, stage 2: morning roll call (parents told at once; it IS the subjects' attendance), the homework diary
(evening message to families), parent-teacher meetings (parents book), and the pages."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.academics.models import CourseOffering
from app.core.clock import institute_today
from app.core.config import settings
from app.notifications.models import Notification
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _login, _login_admin
from tests.integration.test_parent_portal import _parent_login
from tests.integration.test_school_setup import _classes, _pupil, _setup

pytestmark = pytest.mark.asyncio
S = "/api/v1/school"


@pytest.fixture(autouse=True)
def _school(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(settings, "school_mode", 1)


async def _titles(db, user_id):
    return [n.title for n in (await db.execute(select(Notification).where(Notification.user_id == user_id))).scalars()]


async def _class_5a(client, db, *, tag="1"):
    """Class 5-A with a class teacher, two pupils (the first has a parent login)."""
    await _login_admin(client)
    await _setup(client, ["PRIMARY"])
    c5 = (await _classes(client))["C5"]
    sec = c5["sections"][0]["id"]
    ct_user, ct_first = await _staff(client, db, f"CT{tag}", "TEACHER", f"35202-75{tag}0001-1")
    teachers = (await client.get(f"{S}/teachers")).json()
    ct = next(t for t in teachers if t["name"].startswith(f"CT{tag}"))
    await client.patch(f"{S}/sections/{sec}", json={"class_teacher_id": ct["id"]})
    p1, p2 = await _pupil(client, 40 + int(tag) * 2), await _pupil(client, 41 + int(tag) * 2)
    r = await client.post(f"{S}/sections/{sec}/students", json={"student_ids": [p1, p2]})
    assert r.status_code == 200 and not r.json()["failed"], r.text
    made = (await client.post(f"/api/v1/parents/students/{p1}")).json()
    from app.auth.models import User

    parent_id = (await db.execute(select(User.id).where(User.username == made["username"]))).scalar_one()
    return {"c5": c5, "sec": sec, "ct": (ct_user, ct_first, ct["id"]), "pupils": (p1, p2), "parent": (made["username"], made["temporary_password"], parent_id)}


async def test_roll_call_tells_parents_and_is_the_subjects_attendance(app_client, admin_user, db_session):
    w = await _class_5a(app_client, db_session)
    sec, (p1, p2) = w["sec"], w["pupils"]
    ct_user, ct_first, _ = w["ct"]
    other_user, other_first = await _staff(app_client, db_session, "OTH1", "TEACHER", "35202-7590001-1")
    other_id = next(t["id"] for t in (await app_client.get(f"{S}/teachers")).json() if t["name"].startswith("OTH1"))
    parent_id = w["parent"][2]

    ct_pw = await _first_login(app_client, ct_user, ct_first)
    mine = (await app_client.get(f"{S}/roll-call/mine")).json()
    assert [(m["class"], m["label"]) for m in mine] == [("Class 5", "A")]
    sheet = (await app_client.get(f"{S}/roll-call/sections/{sec}")).json()
    assert sheet["can_mark"] is True and sheet["counts"]["unmarked"] == 2

    r = await app_client.put(f"{S}/roll-call/sections/{sec}", json={"entries": [{"student_id": p1, "status": "ABSENT"}, {"student_id": p2, "status": "PRESENT"}]})
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["ABSENT"] == 1 and r.json()["counts"]["PRESENT"] == 1
    assert (await _titles(db_session, parent_id)).count("Absent from school today") == 1
    await app_client.put(f"{S}/roll-call/sections/{sec}", json={"entries": [{"student_id": p1, "status": "ABSENT"}]})
    assert (await _titles(db_session, parent_id)).count("Absent from school today") == 1          # told once
    await app_client.put(f"{S}/roll-call/sections/{sec}", json={"entries": [{"student_id": p1, "status": "PRESENT"}]})
    assert "Present in school today" in await _titles(db_session, parent_id)                     # the correction

    yesterday = (institute_today() - timedelta(days=1)).isoformat()
    late = await app_client.put(f"{S}/roll-call/sections/{sec}", json={"day": yesterday, "entries": [{"student_id": p1, "status": "ABSENT"}]})
    assert late.status_code == 422 and "today" in late.text

    # the subjects' attendance comes from the roll call
    from app.attendance.service import AttendanceService
    from app.school.daily import is_school_subject

    off = (await db_session.execute(select(CourseOffering).where(CourseOffering.section_label == "A", CourseOffering.term_label == "2026-27"))).scalars().first()
    assert await is_school_subject(db_session, off.id)
    await app_client.put(f"{S}/roll-call/sections/{sec}", json={"entries": [{"student_id": p2, "status": "ABSENT"}]})
    pct = await AttendanceService(db_session).compute_percentage(uuid.UUID(p2), off.id)
    assert (pct["total_sessions_taken"], pct["percentage"]) == (1, 0.0)
    assert (await AttendanceService(db_session).compute_percentage(uuid.UUID(p1), off.id))["percentage"] == 100.0

    # another teacher may not, until the office sets them to cover today
    await _first_login(app_client, other_user, other_first)
    assert (await app_client.get(f"{S}/roll-call/sections/{sec}")).status_code == 403
    await _login_admin(app_client)
    assert (await app_client.put(f"{S}/roll-call/sections/{sec}/cover", json={"day": institute_today().isoformat(), "employee_id": other_id})).status_code == 200
    await _login(app_client, other_user, "Chosen#Pass-2026x")
    assert [m["covering"] for m in (await app_client.get(f"{S}/roll-call/mine")).json()] == [True]
    assert (await app_client.put(f"{S}/roll-call/sections/{sec}", json={"entries": [{"student_id": p2, "status": "LATE"}]})).status_code == 200

    # the office: the day's overview, and corrections to an earlier day
    await _login_admin(app_client)
    ov = (await app_client.get(f"{S}/roll-call/overview")).json()
    row = next(x for x in ov["sections"] if x["id"] == sec)
    assert (row["marked"], row["late"], row["cover"]) == (2, 1, "OTH1 X")
    assert (await app_client.put(f"{S}/roll-call/sections/{sec}", json={"day": yesterday, "entries": [{"student_id": p1, "status": "LEAVE"}]})).status_code == 200

    # the parent sees their own child's month; not another child's
    await _parent_login(app_client, w["parent"][0], w["parent"][1])
    month = (await app_client.get(f"{S}/students/{p1}/days")).json()
    assert {d["status"] for d in month["days"]} >= {"PRESENT"}
    assert (await app_client.get(f"{S}/students/{p2}/days")).status_code == 403
    _ = ct_pw


async def test_homework_diary_and_the_evening_message(app_client, admin_user, db_session, monkeypatch):
    w = await _class_5a(app_client, db_session, tag="2")
    sec, (p1, _p2) = w["sec"], w["pupils"]
    st_user, st_first = await _staff(app_client, db_session, "ENGT", "TEACHER", "35202-7690001-1")
    eng_t = next(t["id"] for t in (await app_client.get(f"{S}/teachers")).json() if t["name"].startswith("ENGT"))
    detail = (await app_client.get(f"{S}/sections/{sec}")).json()
    english = next(s for s in detail["subjects"] if s["title"] == "English")
    await app_client.put(f"{S}/sections/{sec}/teachers", json={"teachers": {english["course_id"]: eng_t}})

    await _first_login(app_client, st_user, st_first)
    mine = (await app_client.get(f"{S}/homework/mine")).json()
    assert [(c["class"], c["section"], c["subject"]) for c in mine["classes"]] == [("Class 5", "A", "English")]
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    r = await app_client.post(f"{S}/homework", data={"offering_id": english["offering_id"], "title": "Write ten sentences about your school",
                                                     "due_on": (institute_today() + timedelta(days=2)).isoformat()},
                              files={"file": ("sheet.pdf", pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    hw = r.json()
    assert hw["subject"] == "English" and hw["file_url"]
    assert (await app_client.post(f"{S}/homework", data={"offering_id": english["offering_id"], "title": "x"})).status_code == 422

    # a teacher who does not teach it cannot set it
    ct_user, ct_first, _ = w["ct"]
    await _first_login(app_client, ct_user, ct_first)
    assert (await app_client.post(f"{S}/homework", data={"offering_id": english["offering_id"], "title": "Read chapter 3"})).status_code == 403

    # the parent sees it and can open the file
    await _parent_login(app_client, w["parent"][0], w["parent"][1])
    diary = (await app_client.get(f"{S}/students/{p1}/homework")).json()
    assert [h["title"] for h in diary["homework"]] == ["Write ten sentences about your school"]
    f = await app_client.get(diary["homework"][0]["file_url"])
    assert f.status_code == 200 and f.content.startswith(b"%PDF")

    # one evening message per family
    from app.school.daily import homework_digest

    monkeypatch.setattr(settings, "homework_digest_hour", 0)
    assert await homework_digest(db_session) == 2
    assert await homework_digest(db_session) == 0                                               # once a day
    assert any(t.startswith("Homework for ") for t in await _titles(db_session, w["parent"][2]))

    await _login(app_client, st_user, "Chosen#Pass-2026x")
    assert (await app_client.delete(f"{S}/homework/{hw['id']}")).status_code == 204


async def test_parents_book_a_meeting_with_the_class_teacher(app_client, admin_user, db_session):
    w = await _class_5a(app_client, db_session, tag="3")
    p1 = w["pupils"][0]
    day = (institute_today() + timedelta(days=7)).isoformat()
    r = await app_client.post(f"{S}/ptm", json={"day": day, "start_time": "15:00", "end_time": "16:00", "slot_minutes": 20, "note": "Bring the diary."})
    assert r.status_code == 201, r.text
    ptm = r.json()
    assert ptm["slots"] == 3 and ptm["teachers"][0]["name"].startswith("CT3")
    assert "Parent-teacher meeting" in await _titles(db_session, w["parent"][2])

    await _parent_login(app_client, w["parent"][0], w["parent"][1])
    view = (await app_client.get(f"{S}/children/{p1}/ptm")).json()
    assert view["class_teacher"].startswith("CT3") and len(view["days"][0]["slots"]) == 3
    s1, s2 = view["days"][0]["slots"][0]["id"], view["days"][0]["slots"][1]["id"]
    booked = (await app_client.post(f"{S}/ptm/slots/{s1}/book", json={"student_id": p1})).json()
    assert booked["days"][0]["booked"] == "15:00"
    moved = (await app_client.post(f"{S}/ptm/slots/{s2}/book", json={"student_id": p1})).json()
    assert moved["days"][0]["booked"] == "15:20" and moved["days"][0]["slots"][0]["free"] is True       # one per child: moved
    assert (await app_client.post(f"{S}/ptm/slots/{s1}/book", json={"student_id": w["pupils"][1]})).status_code == 404   # not their child

    ct_user, ct_first, _ = w["ct"]
    await _first_login(app_client, ct_user, ct_first)
    mine = (await app_client.get(f"{S}/ptm/mine")).json()
    slot = next(x for x in mine[0]["slots"] if x["student"])
    assert slot["start_time"] == "15:20"
    assert (await app_client.put(f"{S}/ptm/slots/{slot['id']}/notes", json={"notes": "Reading is improving.", "attended": True})).json()["attended"] is True

    await _login_admin(app_client)
    assert (await app_client.post(f"{S}/ptm/{ptm['id']}/cancel")).json()["cancelled"] is True
    assert "Parent-teacher meeting cancelled" in await _titles(db_session, w["parent"][2])


async def test_pages_and_menus_in_school_mode(app_client, admin_user, db_session, monkeypatch):
    w = await _class_5a(app_client, db_session, tag="4")
    ct_user, ct_first, _ = w["ct"]
    await _first_login(app_client, ct_user, ct_first)
    home = (await app_client.get("/dashboard/faculty", headers={"accept": "text/html"})).text
    assert 'href="/dashboard/faculty/my-class"' in home and 'href="/dashboard/faculty/homework"' in home
    assert 'href="/dashboard/faculty/attendance"' not in home                                     # school classes: the morning roll call
    for path, js in (("/dashboard/faculty/my-class", "school-my-class.js"), ("/dashboard/faculty/homework", "school-homework.js")):
        page = await app_client.get(path, headers={"accept": "text/html"})
        assert page.status_code == 200 and js in page.text, path
    await _parent_login(app_client, w["parent"][0], w["parent"][1])
    for path, js in (("/dashboard/parent/diary", "parent-diary.js"), ("/dashboard/parent/meetings", "parent-meetings.js")):
        page = await app_client.get(path, headers={"accept": "text/html"})
        assert page.status_code == 200 and js in page.text, path
    await _login_admin(app_client)
    page = (await app_client.get("/dashboard/admin/classes", headers={"accept": "text/html"})).text
    assert "school-today.js" in page and "school-ptm.js" in page
    monkeypatch.setattr(settings, "school_mode", 0)
    await _first_login(app_client, *(await _staff(app_client, db_session, "COLT", "TEACHER", "35202-7790001-1")))
    home = (await app_client.get("/dashboard/faculty", headers={"accept": "text/html"})).text
    assert 'href="/dashboard/faculty/my-class"' not in home and 'href="/dashboard/faculty/attendance"' in home


async def test_after_the_first_password_change_the_portal_opens_at_once(app_client, admin_user, db_session, monkeypatch):
    """The account check is cached for a few seconds; an account that had to change its password must not be sent back
    to the change-password page right after changing it."""
    monkeypatch.setattr(settings, "session_check_seconds", 60)
    w = await _class_5a(app_client, db_session, tag="5")
    username, temp, _ = w["parent"]
    await _login(app_client, username, temp)
    assert (await app_client.get("/api/v1/auth/me")).json()["must_change_password"] is True
    assert (await app_client.post("/api/v1/auth/change-password", json={"current_password": temp, "new_password": "Parent#Pass-2026"})).status_code == 204
    assert (await app_client.get("/api/v1/auth/me")).json()["must_change_password"] is False
