"""The Timetable wizard: clearing a batch's weekly timetable from a date to remake it (classes before the date and classes
with attendance are never touched, classes with work are cancelled not deleted, each batch from its own start, finished
batches left alone, combined classes cleared in every section), the readiness check, and the exam department's access."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.academics.models import ClassSession, ClassSessionStatus, TimetableSlot
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _login_admin
from tests.integration.test_smart_timetable import END, START, TERM, TODAY, A, S, _setup
from tests.integration.test_smart_timetable import _q as _q  # noqa: F401  (rate limits off)

pytestmark = pytest.mark.asyncio


async def _slot(client, offering, day, period, room="SR1"):
    r = await client.post(f"{A}/offerings/{offering}/timetable-slots", json={"day_of_week": day, "period_id": period, "room": room})
    assert r.status_code == 201, r.text
    return r.json()["slot"]["id"]


async def _sessions(db, slot_id=None, offering=None):
    q = select(ClassSession).order_by(ClassSession.session_date)
    q = q.where(ClassSession.timetable_slot_id == slot_id) if slot_id else q.where(ClassSession.course_offering_id == offering)
    return list((await db.execute(q)).scalars())


async def test_clear_keeps_held_and_marked_classes_and_removes_empty_future_ones(app_client, admin_user, db_session):
    offs, _t, _l, periods = await _setup(app_client, db_session)
    slot_a = await _slot(app_client, offs["101A"], "MONDAY", periods[0])
    await _slot(app_client, offs["102A"], "TUESDAY", periods[1], room="SR2")
    rows = await _sessions(db_session, slot_a)
    past = [s for s in rows if s.session_date < TODAY]
    future = [s for s in rows if s.session_date > TODAY]
    assert past and len(future) >= 3
    held, marked, topic, empty = past[0], future[0], future[1], future[2]
    held.attendance_taken_at = held.created_at                     # attendance taken last week
    marked.attendance_taken_at = marked.created_at                  # attendance already marked on a later class
    topic.title = "Chapter 4"                                       # the teacher planned this one
    await db_session.commit()
    ids = {"held": held.id, "marked": marked.id, "topic": topic.id, "empty": empty.id}
    before_past = len(past)

    # the confirm word is required, and a past date is refused
    assert (await app_client.post(f"{S}/clear", json={"terms": [TERM], "from_date": str(TODAY), "confirm": "yes"})).status_code == 422
    assert (await app_client.get(f"{S}/clear-preview", params={"terms": TERM, "from_date": str(TODAY - timedelta(days=1))})).status_code == 422

    pv = await app_client.get(f"{S}/clear-preview", params={"terms": TERM, "from_date": str(TODAY + timedelta(days=1))})
    assert pv.status_code == 200, pv.text
    p = pv.json()
    assert p["confirm_word"] == "CLEAR" and len(p["terms"]) == 1 and p["terms"][0]["chosen"]
    t = p["totals"]
    assert t["slots"] == 2 and t["kept_attendance"] == 1 and t["kept_cancelled"] == 1 and t["before"] >= before_past
    assert t["removed"] >= len(future) - 2
    # the preview changes nothing
    assert (await db_session.execute(select(func.count()).select_from(TimetableSlot).where(
        TimetableSlot.course_offering_id.in_([offs["101A"], offs["102A"]])))).scalar_one() == 2

    r = await app_client.post(f"{S}/clear", json={"terms": [TERM], "from_date": str(TODAY + timedelta(days=1)), "confirm": "clear", "notify": []})
    assert r.status_code == 200, r.text
    assert r.json()["weekly_patterns_removed"] == 2
    db_session.expire_all()
    assert (await db_session.execute(select(func.count()).select_from(TimetableSlot).where(
        TimetableSlot.course_offering_id.in_([offs["101A"], offs["102A"]])))).scalar_one() == 0
    got = {k: await db_session.get(ClassSession, v) for k, v in ids.items()}
    assert got["held"] is not None and got["held"].status == ClassSessionStatus.SCHEDULED and got["held"].attendance_taken_at
    assert got["marked"] is not None and got["marked"].status == ClassSessionStatus.SCHEDULED and got["marked"].attendance_taken_at
    assert got["topic"] is not None and got["topic"].status == ClassSessionStatus.CANCELLED
    assert got["empty"] is None
    left = await _sessions(db_session, offering=offs["101A"])
    assert len([s for s in left if s.session_date < TODAY]) == before_past          # every earlier class is still there
    assert all(s.timetable_slot_id is None for s in left)

    # it is recorded, and the readiness check now shows no live timetable
    rd = (await app_client.get(f"{S}/readiness", params={"terms": TERM})).json()
    assert rd["live_weekly_patterns"] == 0 and rd["sections"]["total"] == 5 and rd["sections"]["without_teacher"] == 0
    assert rd["periods"]["class"] >= 6 and rd["rooms"] >= 3


async def test_each_batch_from_its_own_start_and_finished_batches_untouched(app_client, admin_user, db_session):
    offs, _t, _l, periods = await _setup(app_client, db_session)
    course = (await app_client.post(f"{A}/courses", json={"code": "SM201", "title": "Later", "credit_hours": 3})).json()["id"]

    async def offering(term, start, end):
        r = await app_client.post(f"{A}/offerings", json={"course_id": course, "term_label": term, "section_label": "A",
                                                         "term_start_date": str(start), "term_end_date": str(end)})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    later = await offering("Smart Spring", TODAY + timedelta(days=20), END)
    done = await offering("Smart Old", TODAY - timedelta(days=120), TODAY - timedelta(days=10))
    await _slot(app_client, offs["101A"], "MONDAY", periods[0])
    await _slot(app_client, later, "WEDNESDAY", periods[2])
    done_slot = await _slot(app_client, done, "THURSDAY", periods[3])
    done_before = len(await _sessions(db_session, done_slot))

    terms = [TERM, "Smart Spring", "Smart Old"]
    p = (await app_client.get(f"{S}/clear-preview", params={"terms": "||".join(terms), "from_date": str(TODAY)})).json()
    by = {x["term"]: x for x in p["terms"]}
    assert p["ended"] == ["Smart Old"] and "Smart Old" not in by
    assert by["Smart Spring"]["from"] == str(TODAY + timedelta(days=20))          # its own start, not the chosen date
    assert by[TERM]["from"] == str(TODAY)

    r = await app_client.post(f"{S}/clear", json={"terms": terms, "from_date": str(TODAY), "confirm": "CLEAR"})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert await db_session.get(TimetableSlot, done_slot) is not None                  # the finished batch keeps its timetable
    assert len(await _sessions(db_session, done_slot)) == done_before
    assert not await _sessions(db_session, offering=later)                           # nothing had happened yet: all gone


async def test_combined_class_is_cleared_in_every_section(app_client, admin_user, db_session):
    offs, teachers, _l, periods = await _setup(app_client, db_session)
    course = (await app_client.post(f"{A}/courses", json={"code": "SM301", "title": "Shared", "credit_hours": 2})).json()["id"]
    den, ans = "Smart Dental", "Smart Anesthesia"
    ids = {}
    for term in (den, ans):
        r = await app_client.post(f"{A}/offerings", json={"course_id": course, "term_label": term, "section_label": "A",
                                                         "term_start_date": str(START), "term_end_date": str(END)})
        assert r.status_code == 201, r.text
        ids[term] = r.json()["id"]
        assert (await app_client.patch(f"{A}/offerings/{ids[term]}", json={"assigned_teacher_id": teachers["STC"]})).status_code == 200
    c = await app_client.post(f"{A}/offerings/{ids[den]}/combine", json={"offering_ids": [ids[ans]]})
    assert c.status_code == 200, c.text
    await _slot(app_client, ids[den], "FRIDAY", periods[4])
    other = await _slot(app_client, offs["101A"], "MONDAY", periods[0])           # a batch that was not chosen
    # only Dental is chosen, but the combined class is one class: Anesthesia's side goes too, and the preview says so
    p = (await app_client.get(f"{S}/clear-preview", params={"terms": den, "from_date": str(TODAY)})).json()
    by = {x["term"]: x for x in p["terms"]}
    assert by[den]["chosen"] and ans in by and not by[ans]["chosen"] and by[ans]["combined_from"] == [den]
    assert (await app_client.post(f"{S}/clear", json={"terms": [den], "from_date": str(TODAY), "confirm": "CLEAR"})).status_code == 200
    db_session.expire_all()
    assert (await db_session.execute(select(func.count()).select_from(TimetableSlot).where(
        TimetableSlot.course_offering_id.in_(list(ids.values()))))).scalar_one() == 0
    assert await db_session.get(TimetableSlot, other) is not None                 # other batches are not involved


async def test_exam_department_runs_the_timetable_teachers_cannot(app_client, admin_user, db_session):
    offs, _t, logins, periods = await _setup(app_client, db_session)
    exam, first = await _staff(app_client, db_session, "EXTT", "EXAM_CONTROLLER", "35202-8810202-2")
    await _first_login(app_client, exam, first)

    assert (await app_client.get("/dashboard/exam/timetable")).status_code == 200
    assert (await app_client.get(f"{S}/readiness", params={"terms": TERM})).status_code == 200
    assert (await app_client.get(f"{S}/sections", params={"terms": TERM})).status_code == 200
    assert (await app_client.post(f"{A}/periods", json={"period_number": 40, "start_time": "15:00:00", "end_time": "15:50:00"})).status_code in (200, 201)
    assert (await app_client.post(f"{A}/rooms", json={"name": "EX-R1", "kind": "CLASS", "seats": 30})).status_code in (200, 201)
    slot = await _slot(app_client, offs["101A"], "MONDAY", periods[0])
    g = await app_client.post(f"{S}/drafts", json={"terms": [TERM], "days": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]})
    assert g.status_code == 200, g.text
    assert (await app_client.post(f"{S}/clear", json={"terms": [TERM], "from_date": str(TODAY), "confirm": "CLEAR"})).status_code == 200
    db_session.expire_all()
    assert await db_session.get(TimetableSlot, slot) is None

    # a teacher can do none of it
    teacher, tfirst = logins["STA"]
    await _first_login(app_client, teacher, tfirst)
    assert (await app_client.get(f"{S}/readiness", params={"terms": TERM})).status_code == 403
    assert (await app_client.post(f"{S}/clear", json={"terms": [TERM], "from_date": str(TODAY), "confirm": "CLEAR"})).status_code == 403
    assert (await app_client.post(f"{A}/offerings/{offs['101A']}/timetable-slots",
                                  json={"day_of_week": "MONDAY", "period_id": periods[0], "room": "SR1"})).status_code == 403


async def test_old_smart_timetable_link_opens_the_wizard(app_client, admin_user):
    await _login_admin(app_client)
    r = await app_client.get("/dashboard/admin/timetable/smart", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/dashboard/admin/timetable#draft"
    html = (await app_client.get("/dashboard/admin/timetable")).text
    assert 'id="tw-clear-panel"' in html and "/static/js/timetable-wizard.js" in html and "/static/js/tour.js" in html
