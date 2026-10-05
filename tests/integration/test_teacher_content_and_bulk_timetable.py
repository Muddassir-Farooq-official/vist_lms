"""Teachers manage their own course content; admin gets one-click timetable tools."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from app.core.clock import institute_today

from tests.integration.test_module3_sections_allocation import (
    ADMIN,
    FALL_END,
    FALL_START,
    MONDAY,
    SPRING_END,
    SPRING_START,
    _admin,
    _course,
    _login,
    _offering,
    _period,
    _slot,
    _teacher,
)

pytestmark = pytest.mark.asyncio
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture(autouse=True)
def _no_login_throttle(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _teacher_session(client, db, code, cnic, offering_id):
    """Create a teacher, assign them to the offering, and sign them in."""
    tid = await _teacher(client, db, code, cnic)
    emp = (await client.get(f"/api/v1/employees/{tid}")).json()
    p = await client.patch(f"/api/v1/academics/offerings/{offering_id}", json={"assigned_teacher_id": tid})
    assert p.status_code == 200, p.text
    return tid, emp["username"]


async def _sign_in_teacher(client, username):
    await _login(client, username, "TeacherPass123!")
    ch = await client.post("/api/v1/auth/change-password", json={"current_password": "TeacherPass123!", "new_password": "TeacherNew12345!"})
    assert ch.status_code == 204, ch.text
    await _login(client, username, "TeacherNew12345!")


async def _first_sessions(client, offering_id):
    r = await client.get(f"/api/v1/academics/offerings/{offering_id}/class-sessions")
    assert r.status_code == 200, r.text
    return sorted(r.json(), key=lambda s: s["session_date"])


async def _offering_with_slot(client, db, code="TC-101", cnic="35202-8100001-1", tcode="TCH1"):
    await _admin(client)
    cid = await _course(client, code)
    oid = await _offering(client, cid)
    _tid, username = await _teacher_session(client, db, tcode, cnic, oid)
    period = await _period(client, "09:00:00", "10:00:00")
    assert (await _slot(client, oid, period, "R-1")).status_code == 201
    return oid, username


async def test_assigned_teacher_can_set_topics_and_resources_but_not_times(app_client, admin_user, db_session):
    oid, username = await _offering_with_slot(app_client, db_session)
    sessions = await _first_sessions(app_client, oid)
    sid = sessions[0]["id"]
    await _sign_in_teacher(app_client, username)

    ok = await app_client.patch(f"/api/v1/academics/class-sessions/{sid}", json={"title": "Introduction", "description": "Chapter 1"})
    assert ok.status_code == 200 and ok.json()["title"] == "Introduction"
    # date / time / room are the admin's
    for body in ({"room": "R-9"}, {"session_date": str(FALL_START + timedelta(days=1))}, {"start_time": "10:00:00"}):
        r = await app_client.patch(f"/api/v1/academics/class-sessions/{sid}", json=body)
        assert r.status_code == 403 and "Only the admin" in r.text

    link = await app_client.post(f"/api/v1/academics/class-sessions/{sid}/resources/link",
                                 json={"resource_type": "SLIDES", "external_url": "https://example.com/deck", "title": "Week 1 slides"})
    assert link.status_code == 201, link.text
    up = await app_client.post(f"/api/v1/academics/class-sessions/{sid}/resources", params={"resource_type": "NOTES"},
                               files={"file": ("notes.pdf", PDF, "application/pdf")})
    assert up.status_code == 201, up.text
    listed = (await app_client.get(f"/api/v1/academics/offerings/{oid}/class-sessions")).json()
    assert len([r for r in listed[0]["resources"]]) == 2
    assert (await app_client.delete(f"/api/v1/academics/class-session-resources/{link.json()['id']}")).status_code == 204


async def test_teacher_can_manage_syllabus_of_own_course(app_client, admin_user, db_session):
    oid, username = await _offering_with_slot(app_client, db_session)
    await _sign_in_teacher(app_client, username)
    up = await app_client.post(f"/api/v1/academics/offerings/{oid}/syllabus", files={"file": ("syllabus.pdf", PDF, "application/pdf")})
    assert up.status_code == 200 and up.json()["has_syllabus"] is True
    assert (await app_client.get(f"/api/v1/academics/offerings/{oid}/syllabus")).status_code == 200
    rm = await app_client.delete(f"/api/v1/academics/offerings/{oid}/syllabus")
    assert rm.status_code == 200 and rm.json()["has_syllabus"] is False


async def test_other_teachers_and_students_cannot_change_someone_elses_content(app_client, admin_user, db_session):
    oid, _owner = await _offering_with_slot(app_client, db_session)
    sid = (await _first_sessions(app_client, oid))[0]["id"]
    _t2, other = await _teacher_session(app_client, db_session, "TCH2", "35202-8100002-2", await _offering(app_client, await _course(app_client, "TC-201")))
    period = await _period(app_client, "11:00:00", "12:00:00")          # admin sets this up BEFORE signing in as a teacher
    await _sign_in_teacher(app_client, other)
    for r in (
        await app_client.patch(f"/api/v1/academics/class-sessions/{sid}", json={"title": "Hijack"}),
        await app_client.post(f"/api/v1/academics/class-sessions/{sid}/resources/link", json={"resource_type": "SLIDES", "external_url": "https://x.example", "title": "x"}),
        await app_client.post(f"/api/v1/academics/offerings/{oid}/syllabus", files={"file": ("s.pdf", PDF, "application/pdf")}),
        await app_client.post(f"/api/v1/academics/offerings/{oid}/lecture-plan", json={"items": [{"class_session_id": sid, "title": "x"}]}),
    ):
        assert r.status_code == 403, r.text
    # and a teacher still cannot touch the timetable itself
    assert (await _slot(app_client, oid, period, "R-7")).status_code == 403
    assert (await app_client.post(f"/api/v1/academics/class-sessions/{sid}/cancel", json={"reason": "x"})).status_code == 403


async def test_admin_can_still_edit_everything(app_client, admin_user, db_session):
    oid, _ = await _offering_with_slot(app_client, db_session)
    sid = (await _first_sessions(app_client, oid))[0]["id"]
    r = await app_client.patch(f"/api/v1/academics/class-sessions/{sid}", json={"title": "By admin", "room": "R-2"})
    assert r.status_code == 200 and r.json()["room"] == "R-2"


async def test_lecture_plan_saves_many_topics_at_once(app_client, admin_user, db_session):
    oid, username = await _offering_with_slot(app_client, db_session)
    sessions = await _first_sessions(app_client, oid)
    await app_client.post(f"/api/v1/academics/class-sessions/{sessions[2]['id']}/cancel", json={"reason": "holiday"})
    await _sign_in_teacher(app_client, username)
    items = [{"class_session_id": s["id"], "title": f"Topic {i + 1}"} for i, s in enumerate(sessions[:4])]
    r = await app_client.post(f"/api/v1/academics/offerings/{oid}/lecture-plan", json={"items": items})
    assert r.status_code == 200 and r.json()["updated"] == 3          # the cancelled class is skipped
    after = {s["id"]: s["title"] for s in await _first_sessions(app_client, oid)}
    assert after[sessions[0]["id"]] == "Topic 1" and after[sessions[3]["id"]] == "Topic 4" and after[sessions[2]["id"]] is None
    blank = await app_client.post(f"/api/v1/academics/offerings/{oid}/lecture-plan", json={"items": [{"class_session_id": sessions[0]["id"], "title": "  "}]})
    assert blank.status_code == 200
    assert {s["id"]: s["title"] for s in await _first_sessions(app_client, oid)}[sessions[0]["id"]] is None
    # a session from another course is refused
    other_oid = None
    await _admin(app_client)
    other_oid = await _offering(app_client, await _course(app_client, "TC-301"))
    period = await _period(app_client, "13:00:00", "14:00:00")
    await _slot(app_client, other_oid, period, "R-5")
    foreign = (await _first_sessions(app_client, other_oid))[0]["id"]
    await _login(app_client, username, "TeacherNew12345!")
    bad = await app_client.post(f"/api/v1/academics/offerings/{oid}/lecture-plan", json={"items": [{"class_session_id": foreign, "title": "x"}]})
    assert bad.status_code == 422 and "does not belong" in bad.text


async def test_auto_schedule_whole_term_then_rerun_is_safe(app_client, admin_user, db_session):
    await _admin(app_client)
    await _period(app_client, "09:00:00", "10:00:00")
    await _period(app_client, "10:00:00", "11:00:00")
    for i in range(3):
        await _offering(app_client, await _course(app_client, f"BLK-10{i}", credit=3))
    body = {"term_label": "fall 2026", "rooms": ["R-1", "R-2"], "sessions_per_week": 2}
    r = await app_client.post("/api/v1/academics/timetable/auto-schedule-term", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["scheduled"]) == 3 and out["failed"] == [] and all(x["classes_per_week"] == 2 for x in out["scheduled"])
    again = (await app_client.post("/api/v1/academics/timetable/auto-schedule-term", json=body)).json()
    assert again["scheduled"] == [] and len(again["skipped"]) == 3
    nothing = await app_client.post("/api/v1/academics/timetable/auto-schedule-term", json={**body, "term_label": "No Such Term"})
    assert nothing.status_code == 404
    empty = await app_client.post("/api/v1/academics/timetable/auto-schedule-term", json={**body, "rooms": ["  "]})
    assert empty.status_code == 422


async def test_auto_schedule_defaults_to_credit_hours_and_reports_what_cannot_fit(app_client, admin_user, db_session):
    await _admin(app_client)
    await _period(app_client, "09:00:00", "10:00:00")
    await _offering(app_client, await _course(app_client, "CRD-101", credit=3))
    r = (await app_client.post("/api/v1/academics/timetable/auto-schedule-term", json={"term_label": "Fall 2026", "rooms": ["R-1"]})).json()
    assert r["scheduled"][0]["classes_per_week"] == 3
    # one room, one period, five weekdays: the fourth course cannot all fit -> reported, never crashes
    for i in range(6):
        await _offering(app_client, await _course(app_client, f"FIT-10{i}", credit=3), section="A")
    r2 = (await app_client.post("/api/v1/academics/timetable/auto-schedule-term", json={"term_label": "Fall 2026", "rooms": ["R-1"]})).json()
    assert r2["failed"] and all(f["reason"] for f in r2["failed"])
    assert len(r2["scheduled"]) + len(r2["failed"]) == 6


async def test_copy_timetable_from_previous_term(app_client, admin_user, db_session):
    await _admin(app_client)
    period = await _period(app_client, "09:00:00", "10:00:00")
    c1 = await _course(app_client, "CPY-100")
    c2 = await _course(app_client, "CPY-101")
    c3 = await _course(app_client, "CPY-102")
    for cid, room in ((c1, "R-1"), (c2, "R-2")):
        oid = await _offering(app_client, cid, term="Fall 2026")
        assert (await _slot(app_client, oid, period, room)).status_code == 201
    await _offering(app_client, c3, term="Fall 2026")                                    # had NO timetable last term
    for cid in (c1, c2, c3):
        await _offering(app_client, cid, term="Spring 2027", start=SPRING_START, end=SPRING_END)
    await _offering(app_client, await _course(app_client, "CPY-NEW"), term="Spring 2027", start=SPRING_START, end=SPRING_END)  # brand-new course

    r = await app_client.post("/api/v1/academics/timetable/copy-from-term", json={"source_term": "fall 2026", "target_term": "Spring 2027"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert sorted(x["course_code"] for x in out["scheduled"]) == ["CPY-100", "CPY-101"]
    reasons = {f["course_code"]: f["reason"] for f in out["failed"]}
    assert "had no timetable" in reasons["CPY-102"] and "not offered in Fall 2026" in reasons["CPY-NEW"]
    spring = (await app_client.get("/api/v1/academics/offerings", params={"term_label": "Spring 2027"})).json()
    by_code = {o["course_code"]: o["id"] for o in spring}
    slots = (await app_client.get(f"/api/v1/academics/offerings/{by_code['CPY-100']}/timetable-slots")).json()
    assert len(slots) == 1 and slots[0]["room"] == "R-1" and slots[0]["day_of_week"] == MONDAY
    again = (await app_client.post("/api/v1/academics/timetable/copy-from-term", json={"source_term": "Fall 2026", "target_term": "Spring 2027"})).json()
    assert len(again["skipped"]) == 2                                                    # safe to run twice
    assert (await app_client.post("/api/v1/academics/timetable/copy-from-term", json={"source_term": "Fall 2026", "target_term": "fall 2026"})).status_code == 422


async def test_only_admin_can_use_bulk_timetable_tools(app_client, admin_user, db_session):
    oid, username = await _offering_with_slot(app_client, db_session)
    await _sign_in_teacher(app_client, username)
    assert (await app_client.post("/api/v1/academics/timetable/auto-schedule-term", json={"term_label": "Fall 2026", "rooms": ["R-1"]})).status_code == 403
    assert (await app_client.post("/api/v1/academics/timetable/copy-from-term", json={"source_term": "A", "target_term": "B"})).status_code == 403


async def test_term_readiness_flags_classes_without_a_topic(app_client, admin_user, db_session):
    await _admin(app_client)
    cid = await _course(app_client, "RDY-101")
    today = institute_today()
    oid = await _offering(app_client, cid, start=today, end=today + timedelta(days=60))
    period = await _period(app_client, "09:00:00", "10:00:00")
    day = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"][(today + timedelta(days=1)).weekday()]
    assert (await _slot(app_client, oid, period, "R-1", day=day)).status_code == 201
    row = (await app_client.get("/api/v1/academics/allocation-overview", params={"term_label": "Fall 2026"})).json()["sections"][0]
    assert "TOPICS_MISSING" in row["issues"] and row["topics_missing_next_14_days"] >= 2
    sessions = await _first_sessions(app_client, oid)
    items = [{"class_session_id": s["id"], "title": "Planned"} for s in sessions]
    await app_client.post(f"/api/v1/academics/offerings/{oid}/lecture-plan", json={"items": items})
    row = (await app_client.get("/api/v1/academics/allocation-overview", params={"term_label": "Fall 2026"})).json()["sections"][0]
    assert "TOPICS_MISSING" not in row["issues"] and row["topics_missing_next_14_days"] == 0


# ===========================================================================
# Admin can lock / unlock content; completed topics are visible to everyone
# ===========================================================================
_DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


async def _offering_spanning_today(client, db, code="LK-101", cnic="35202-8200001-1", tcode="LKT1"):
    """An offering with one weekly class that has classes BEFORE today and AFTER today."""
    await _admin(client)
    today = institute_today()
    cid = await _course(client, code)
    oid = await _offering(client, cid, start=today - timedelta(days=21), end=today + timedelta(days=28))
    _tid, username = await _teacher_session(client, db, tcode, cnic, oid)
    period = await _period(client, "09:00:00", "10:00:00")
    assert (await _slot(client, oid, period, "R-1", day=_DAYS[today.weekday()])).status_code == 201
    sessions = await _first_sessions(client, oid)
    past = [s for s in sessions if s["session_date"] < str(today)]
    upcoming = [s for s in sessions if s["session_date"] >= str(today)]
    assert past and upcoming
    return oid, username, past, upcoming


async def _teacher_adds_content(client, session_id):
    return await client.post(f"/api/v1/academics/class-sessions/{session_id}/resources/link",
                             json={"resource_type": "SLIDES", "external_url": "https://x.example/deck", "title": "Deck"})


async def test_locking_the_whole_course_protects_syllabus_topics_and_files(app_client, admin_user, db_session):
    oid, username, past, upcoming = await _offering_with_slot_spanning(app_client, db_session)
    sid = past[0]["id"]
    await _sign_in_teacher(app_client, username)
    link = await _teacher_adds_content(app_client, sid)
    assert link.status_code == 201
    await app_client.post(f"/api/v1/academics/offerings/{oid}/syllabus", files={"file": ("s.pdf", PDF, "application/pdf")})

    await _admin(app_client)
    lock = await app_client.post(f"/api/v1/academics/offerings/{oid}/content-lock", json={"locked": True, "scope": "COURSE"})
    assert lock.status_code == 200 and lock.json()["content_locked"] is True
    offering = (await app_client.get(f"/api/v1/academics/offerings/{oid}")).json()
    assert offering["content_locked"] is True

    await _login(app_client, username, "TeacherNew12345!")
    for r in (
        await app_client.patch(f"/api/v1/academics/class-sessions/{sid}", json={"title": "Changed"}),
        await _teacher_adds_content(app_client, upcoming[0]["id"]),
        await app_client.delete(f"/api/v1/academics/class-session-resources/{link.json()['id']}"),
        await app_client.delete(f"/api/v1/academics/offerings/{oid}/syllabus"),
        await app_client.post(f"/api/v1/academics/offerings/{oid}/syllabus", files={"file": ("s2.pdf", PDF, "application/pdf")}),
        await app_client.post(f"/api/v1/academics/offerings/{oid}/lecture-plan", json={"items": [{"class_session_id": sid, "title": "x"}]}),
    ):
        assert r.status_code == 403 and "locked" in r.text, r.text
    # the teacher can still SEE everything, and still record that a topic was covered
    seen = (await app_client.get(f"/api/v1/academics/offerings/{oid}/class-sessions")).json()
    assert any(res for s in seen for res in s["resources"])
    covered = await app_client.post(f"/api/v1/academics/class-sessions/{sid}/topic-completed", json={"completed": True})
    assert covered.status_code == 200 and covered.json()["topic_completed"] is True

    # the admin is never blocked, and unlocking restores the teacher's access
    await _admin(app_client)
    assert (await app_client.patch(f"/api/v1/academics/class-sessions/{sid}", json={"title": "Admin edit"})).status_code == 200
    assert (await app_client.post(f"/api/v1/academics/offerings/{oid}/content-lock", json={"locked": False, "scope": "COURSE"})).json()["content_locked"] is False
    await _login(app_client, username, "TeacherNew12345!")
    assert (await app_client.patch(f"/api/v1/academics/class-sessions/{sid}", json={"title": "Teacher again"})).status_code == 200
    assert (await app_client.delete(f"/api/v1/academics/class-session-resources/{link.json()['id']}")).status_code == 204


async def _offering_with_slot_spanning(client, db):
    oid, username, past, upcoming = await _offering_spanning_today(client, db)
    return oid, username, past, upcoming


async def test_lock_past_classes_only_leaves_upcoming_classes_editable(app_client, admin_user, db_session):
    oid, username, past, upcoming = await _offering_spanning_today(app_client, db_session, code="LK-201", cnic="35202-8200002-2", tcode="LKT2")
    await _sign_in_teacher(app_client, username)
    res = await _teacher_adds_content(app_client, past[0]["id"])
    assert res.status_code == 201

    await _admin(app_client)
    locked = await app_client.post(f"/api/v1/academics/offerings/{oid}/content-lock", json={"locked": True, "scope": "PAST_CLASSES"})
    assert locked.status_code == 200 and locked.json()["classes_changed"] == len(past) and locked.json()["content_locked"] is False
    again = await app_client.post(f"/api/v1/academics/offerings/{oid}/content-lock", json={"locked": True, "scope": "PAST_CLASSES"})
    assert again.json()["classes_changed"] == 0                                   # idempotent

    await _login(app_client, username, "TeacherNew12345!")
    # old lecture material can no longer be edited or deleted...
    assert (await app_client.delete(f"/api/v1/academics/class-session-resources/{res.json()['id']}")).status_code == 403
    assert (await app_client.patch(f"/api/v1/academics/class-sessions/{past[0]['id']}", json={"title": "Rewrite history"})).status_code == 403
    assert (await _teacher_adds_content(app_client, past[-1]["id"])).status_code == 403
    # ...while upcoming classes stay fully editable
    assert (await app_client.patch(f"/api/v1/academics/class-sessions/{upcoming[0]['id']}", json={"title": "Next lecture"})).status_code == 200
    assert (await _teacher_adds_content(app_client, upcoming[0]["id"])).status_code == 201
    flags = {s["id"]: s["content_locked"] for s in await _first_sessions(app_client, oid)}
    assert all(flags[s["id"]] for s in past) and not any(flags[s["id"]] for s in upcoming)

    # a pasted plan quietly skips locked classes and says how many
    plan = await app_client.post(f"/api/v1/academics/offerings/{oid}/lecture-plan", json={"items": [
        {"class_session_id": past[0]["id"], "title": "Blocked"}, {"class_session_id": upcoming[0]["id"], "title": "Allowed"}]})
    assert plan.status_code == 200 and plan.json() == {"updated": 1, "skipped_locked": 1}

    await _admin(app_client)
    un = await app_client.post(f"/api/v1/academics/offerings/{oid}/content-lock", json={"locked": False, "scope": "ALL_CLASSES"})
    assert un.json()["classes_changed"] == len(past)
    await _login(app_client, username, "TeacherNew12345!")
    assert (await app_client.patch(f"/api/v1/academics/class-sessions/{past[0]['id']}", json={"title": "Fixed a typo"})).status_code == 200


async def test_lock_and_unlock_one_class_and_only_admin_may(app_client, admin_user, db_session):
    oid, username, past, upcoming = await _offering_spanning_today(app_client, db_session, code="LK-301", cnic="35202-8200003-3", tcode="LKT3")
    sid, other = upcoming[0]["id"], upcoming[1]["id"]
    one = await app_client.post(f"/api/v1/academics/class-sessions/{sid}/content-lock", json={"locked": True})
    assert one.status_code == 200 and one.json()["content_locked"] is True
    await _sign_in_teacher(app_client, username)
    assert (await app_client.patch(f"/api/v1/academics/class-sessions/{sid}", json={"title": "x"})).status_code == 403
    assert (await app_client.patch(f"/api/v1/academics/class-sessions/{other}", json={"title": "fine"})).status_code == 200
    # a teacher cannot lock or unlock anything, including their own classes
    for r in (
        await app_client.post(f"/api/v1/academics/class-sessions/{sid}/content-lock", json={"locked": False}),
        await app_client.post(f"/api/v1/academics/offerings/{oid}/content-lock", json={"locked": False, "scope": "COURSE"}),
    ):
        assert r.status_code == 403
    await _admin(app_client)
    assert (await app_client.post(f"/api/v1/academics/class-sessions/{sid}/content-lock", json={"locked": False})).json()["content_locked"] is False
    bad = await app_client.post(f"/api/v1/academics/offerings/{oid}/content-lock", json={"locked": True, "scope": "EVERYTHING"})
    assert bad.status_code == 422


async def test_students_see_which_topics_the_teacher_has_covered(app_client, admin_user, db_session):
    from tests.integration.test_attendance_and_grading import _create_n_sessions, _setup_offering_with_enrolled_students

    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="CVR1", n_students=1)
    _sid, student_username, _ = studs[0]
    sessions = await _create_n_sessions(app_client, offering_id, 3)
    _tid, teacher_username = await _teacher_session(app_client, db_session, "CVT1", "35202-8300001-1", offering_id)
    await _sign_in_teacher(app_client, teacher_username)
    assert (await app_client.post(f"/api/v1/academics/class-sessions/{sessions[0]}/topic-completed", json={"completed": True})).status_code == 200
    assert (await app_client.post(f"/api/v1/academics/class-sessions/{sessions[1]}/topic-completed", json={"completed": True})).status_code == 200
    assert (await app_client.post(f"/api/v1/academics/class-sessions/{sessions[1]}/topic-completed", json={"completed": False})).status_code == 200   # can be undone

    await _login(app_client, student_username, "StudentPass123!")
    everything = {s["id"]: s["topic_completed"] for s in (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/class-sessions")).json()}
    assert {k: everything[k] for k in sessions} == {sessions[0]: True, sessions[1]: False, sessions[2]: False}
    # a student cannot mark a topic as covered
    assert (await app_client.post(f"/api/v1/academics/class-sessions/{sessions[2]}/topic-completed", json={"completed": True})).status_code == 403