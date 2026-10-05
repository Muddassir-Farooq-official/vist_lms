"""Speed guards (a big term must not take minutes), correctness of the batched lists,
and the shared loading feedback layer."""
from __future__ import annotations

import time

import pytest

from tests.integration.test_attendance_and_grading import _create_n_sessions, _setup_offering_with_enrolled_students
from tests.integration.test_module3_sections_allocation import (
    _admin,
    _course,
    _offering,
    _period,
    _program,
    _student,
    _teacher,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _no_login_throttle(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def test_scheduling_a_big_term_takes_seconds_not_minutes(app_client, admin_user, db_session):
    """Regression: the whole-term scheduler re-ran the same lookups hundreds of thousands of times
    and never finished on a school-sized term."""
    await _admin(app_client)
    for i in range(6):
        await _period(app_client, f"{8 + i:02d}:00:00", f"{9 + i:02d}:00:00")
    for i in range(30):
        await _offering(app_client, await _course(app_client, f"BIG-{i:03d}", credit=3))
    rooms = [f"R-{n}" for n in range(1, 9)]
    started = time.perf_counter()
    r = await app_client.post("/api/v1/academics/timetable/auto-schedule-term", json={"term_label": "Fall 2026", "rooms": rooms, "sessions_per_week": 2})
    elapsed = time.perf_counter() - started
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["scheduled"]) + len(out["failed"]) == 30 and len(out["scheduled"]) >= 25
    assert elapsed < 25, f"scheduling 30 courses took {elapsed:.1f}s"
    # and the result is genuinely clash-free: no room is double-booked at the same time
    slots = (await app_client.get("/api/v1/academics/timetable-slots")).json()
    seen = set()
    for sl in slots:
        key = (sl["day_of_week"], sl["start_time"], sl["room"])
        assert key not in seen, f"room double-booked: {key}"
        seen.add(key)


async def test_offering_and_slot_lists_are_complete_after_batching(app_client, admin_user, db_session):
    await _admin(app_client)
    prog = await _program(db_session)
    tid = await _teacher(app_client, db_session, "BAT", "35202-9700001-1")
    cid = await _course(app_client, "BAT-101", str(prog.id), 1)
    oid = await _offering(app_client, cid, teacher=tid)
    period = await _period(app_client, "09:00:00", "10:00:00")
    from tests.integration.test_module3_sections_allocation import _slot

    assert (await _slot(app_client, oid, period, "R-1")).status_code == 201
    students = [await _student(db_session, n, prog) for n in (1, 2)]
    for st in students:
        assert (await app_client.post(f"/api/v1/academics/offerings/{oid}/enroll-student", json={"student_id": str(st.id)})).status_code == 201
    listing = (await app_client.get("/api/v1/academics/offerings", params={"term_label": "Fall 2026"})).json()
    assert len(listing) == 1
    row = listing[0]
    assert (row["course_code"], row["enrolled_count"], row["section_label"]) == ("BAT-101", 2, "A")
    assert row["teacher_name"] and row["credit_hours"] == 3
    slots = (await app_client.get("/api/v1/academics/timetable-slots")).json()
    assert len(slots) == 1 and slots[0]["course_code"] == "BAT-101" and slots[0]["teacher_name"] == row["teacher_name"]
    ov = (await app_client.get("/api/v1/academics/allocation-overview", params={"term_label": "Fall 2026"})).json()["sections"][0]
    assert (ov["course_code"], ov["enrolled"], ov["slots"], ov["teacher_name"] is not None) == ("BAT-101", 2, 1, True)


async def test_teacher_performance_counts_are_right(app_client, admin_user, db_session):
    await _admin(app_client)
    prog = await _program(db_session)
    ta = await _teacher(app_client, db_session, "TPA", "35202-9700002-1")
    tb = await _teacher(app_client, db_session, "TPB", "35202-9700003-1")
    o1 = await _offering(app_client, await _course(app_client, "TP-101", str(prog.id), 1), teacher=ta)
    o2 = await _offering(app_client, await _course(app_client, "TP-102", str(prog.id), 1), teacher=ta)
    o3 = await _offering(app_client, await _course(app_client, "TP-103", str(prog.id), 1), teacher=tb)
    s1, s2, s3 = [await _student(db_session, n, prog) for n in (1, 2, 3)]
    for oid, st in ((o1, s1), (o1, s2), (o2, s3), (o3, s1)):
        assert (await app_client.post(f"/api/v1/academics/offerings/{oid}/enroll-student", json={"student_id": str(st.id)})).status_code == 201
    rows = {r["teacher_id"]: r for r in (await app_client.get("/api/v1/academics/teacher-performance")).json()}
    assert (rows[ta]["offerings_count"], rows[ta]["total_enrollments"], rows[ta]["complaints_against"]) == (2, 3, 0)
    assert (rows[tb]["offerings_count"], rows[tb]["total_enrollments"], rows[tb]["pass_rate"]) == (1, 1, None)


async def test_needs_attention_is_capped_but_says_how_many_there_are(app_client, admin_user, db_session):
    offering_id, _ = await _setup_offering_with_enrolled_students(app_client, code="CAP1", n_students=1)
    await _create_n_sessions(app_client, offering_id, 5)
    everything = await app_client.get("/api/v1/attendance/needs-attention")
    total = int(everything.headers["X-Total-Count"])
    assert total >= 5 and len(everything.json()) == total
    capped = await app_client.get("/api/v1/attendance/needs-attention", params={"limit": 2})
    assert len(capped.json()) == 2 and int(capped.headers["X-Total-Count"]) == total
    dates = [x["session_date"] for x in capped.json()]
    assert dates == sorted(dates)                                            # oldest first
    assert len((await app_client.get("/api/v1/attendance/needs-attention", params={"limit": 0})).json()) == 1   # never zero / negative


async def test_loading_feedback_is_shipped_to_every_page(app_client):
    css = (await app_client.get("/static/css/dashboard.css")).text
    for needle in ("#net-progress", "html.net-busy", ".skeleton-row", ".skeleton-line", ".is-loading", "#net-slow", ".dlg-progress", "net-refreshing"):
        assert needle in css, needle
    js = (await app_client.get("/static/js/admin-common.js")).text
    for needle in ("netStart", "netDone", "insertSkeletons", "AbortController", "Can't reach the server", "took too long",
                   "Still working", "is-loading", "showModal", "gate-retry"):
        assert needle in js, needle
    assert "headers: resp.headers" in js
    for path, needle in (("/dashboard/student", "skeleton-stack"), ("/dashboard/faculty", "skeleton-stack"),
                         ("/dashboard/admin/timetable", "tt-sheet is-loading")):
        assert needle in (await app_client.get(path)).text, path
    assert "needs-attention-cap" in (await app_client.get("/dashboard/admin/attendance")).text