"""Data behind the student Home page: teacher on each class, and the one-call course overview."""
from __future__ import annotations

import pytest

from tests.integration.test_attendance_and_grading import (
    _create_n_sessions,
    _login,
    _login_admin,
    _setup_offering_with_enrolled_students,
)
from tests.integration.test_module3_sections_allocation import _teacher

pytestmark = pytest.mark.asyncio
NOTE = "seeded for a test"


@pytest.fixture(autouse=True)
def _no_login_throttle(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _mark(client, session_id, present):
    r = await client.post(f"/api/v1/attendance/sessions/{session_id}/mark", json={"present_student_ids": present, "late_note": NOTE})
    assert r.status_code == 200, r.text


async def test_overview_counts_attendance_and_syllabus_progress(app_client, admin_user, db_session):
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="HOME1", n_students=2)
    (s1, u1, _), (s2, u2, _) = studs
    tid = await _teacher(app_client, db_session, "HMT", "35202-9900001-1")
    assert (await app_client.patch(f"/api/v1/academics/offerings/{offering_id}", json={"assigned_teacher_id": tid})).status_code == 200
    sessions = await _create_n_sessions(app_client, offering_id, 4)
    await _mark(app_client, sessions[0], [s1, s2])
    await _mark(app_client, sessions[1], [s2])                       # s1 absent
    await _mark(app_client, sessions[2], [s1, s2])
    assert (await app_client.post(f"/api/v1/academics/class-sessions/{sessions[0]}/topic-completed", json={"completed": True})).status_code == 200
    assert (await app_client.post(f"/api/v1/academics/class-sessions/{sessions[3]}/cancel", json={"reason": "holiday"})).status_code == 200

    await _login(app_client, u1, "StudentPass123!")
    rows = (await app_client.get("/api/v1/academics/enrollments/me/overview")).json()
    assert len(rows) == 1
    row = rows[0]
    assert (row["course_code"].startswith("HOME1"), row["classes_held"], row["classes_attended"], row["attendance_percentage"]) == (True, 3, 2, 66.7)
    assert row["teacher_name"] and row["section_label"] == "A"
    assert row["topics_covered"] == 1 and row["topics_total"] >= 3          # the cancelled class is not counted
    await _login(app_client, u2, "StudentPass123!")
    mine = (await app_client.get("/api/v1/academics/enrollments/me/overview")).json()[0]
    assert (mine["classes_held"], mine["classes_attended"], mine["attendance_percentage"]) == (3, 3, 100.0)   # each student sees their OWN numbers


async def test_a_course_with_no_classes_yet_has_no_percentage(app_client, admin_user, db_session):
    _offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="HOME2", n_students=1)
    await _login(app_client, studs[0][1], "StudentPass123!")
    row = (await app_client.get("/api/v1/academics/enrollments/me/overview")).json()[0]
    assert row["attendance_percentage"] is None and row["classes_held"] == 0


async def test_only_students_use_the_overview_and_dropped_courses_are_left_out(app_client, admin_user, db_session):
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="HOME3", n_students=1)
    await _login_admin(app_client)
    assert (await app_client.get("/api/v1/academics/enrollments/me/overview")).status_code == 403
    enrollment = (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/roster")).json()[0]
    assert (await app_client.post(f"/api/v1/academics/enrollments/{enrollment['id']}/admin-withdraw")).status_code == 200
    await _login(app_client, studs[0][1], "StudentPass123!")
    assert (await app_client.get("/api/v1/academics/enrollments/me/overview")).json() == []


async def test_each_upcoming_class_names_its_teacher_and_section(app_client, admin_user, db_session):
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="HOME4", n_students=1)
    tid = await _teacher(app_client, db_session, "HMU", "35202-9900002-2")
    await app_client.patch(f"/api/v1/academics/offerings/{offering_id}", json={"assigned_teacher_id": tid})
    await _create_n_sessions(app_client, offering_id, 2)
    await _login(app_client, studs[0][1], "StudentPass123!")
    entries = (await app_client.get("/api/v1/academics/enrollments/me/schedule")).json()
    assert entries and all(e["teacher_name"] and e["section_label"] == "A" for e in entries)


async def test_student_home_ships_the_dashboard_cards_and_header_identity(app_client):
    html = (await app_client.get("/dashboard/student")).text
    for needle in ("kpi-row", 'id="class-list"', 'id="attn-list"', 'id="course-cards"', "dash-actions", "Alerts &amp; deadlines", "portal-footer",
                   "weekly.pdf", "/dashboard/student/library", "VIST LMS Student", "Vertex Institute of Science", "Nothing needs your attention"):
        assert needle in html, needle
    js = (await app_client.get("/static/js/student-home.js")).text
    for needle in ("enrollments/me/overview", "enrollments/me/schedule", "attendance/config", "Fee payment due", "Overdue book", "calendar.ics", "results/me/transcript"):
        assert needle in js, needle
    shared = (await app_client.get("/static/js/student-ui.js")).text
    for needle in ("classCard", "calendar.ics", "downloadFile", "Add to Calendar", "function ring"):
        assert needle in shared, needle
    css = (await app_client.get("/static/css/dashboard.css")).text
    for needle in (".sh-ring", ".sh-class.is-next", ".sh-alert.red", ".whoami-text", "scroll-snap-type"):
        assert needle in css, needle
    common = (await app_client.get("/static/js/admin-common.js")).text
    for needle in ("paintWhoami", "loadIdentity", "/api/v1/students/me", "/api/v1/employees/me", "initialsOf"):
        assert needle in common, needle


async def test_home_cards_load_independently_so_one_slow_request_cannot_hold_back_the_page(app_client):
    js = (await app_client.get("/static/js/portal-home.js")).text
    assert "Every card loads and shows on its own" in js
    assert "Promise.all([" not in js.split("function studentHome()")[1].split("function facultyHome()")[0].split("const thresholdOf")[0]   # no all-at-once wait up front
    assert "Checking for more" in js and "const settle = function" in js        # alerts fill in as each source answers


async def test_teacher_overview_shows_each_course_at_a_glance(app_client, admin_user, db_session):
    from tests.integration.test_teacher_content_and_bulk_timetable import _sign_in_teacher, _teacher_session

    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="TOV1", n_students=2)
    (s1, _u1, _), (s2, _u2, _) = studs
    _tid, teacher_username = await _teacher_session(app_client, db_session, "TOVT", "35202-9900003-3", offering_id)
    sessions = await _create_n_sessions(app_client, offering_id, 4)
    await _mark(app_client, sessions[0], [s1, s2])
    await _mark(app_client, sessions[1], [s1])                        # s2 absent  -> 3 present out of 4 records = 75%
    assert (await app_client.post(f"/api/v1/academics/class-sessions/{sessions[0]}/topic-completed", json={"completed": True})).status_code == 200
    assert (await app_client.post(f"/api/v1/academics/class-sessions/{sessions[3]}/cancel", json={"reason": "holiday"})).status_code == 200

    await _sign_in_teacher(app_client, teacher_username)
    # a course whose term ended long ago is not part of "what I teach now"
    assert (await app_client.get("/api/v1/academics/offerings/me/overview")).json() == []
    await _login_admin(app_client)
    from datetime import timedelta

    from app.core.clock import institute_today

    extend = await app_client.patch(f"/api/v1/academics/offerings/{offering_id}", json={"term_end_date": str(institute_today() + timedelta(days=60))})
    assert extend.status_code == 200, extend.text
    await _login(app_client, teacher_username, "TeacherNew12345!")
    rows = (await app_client.get("/api/v1/academics/offerings/me/overview")).json()
    assert len(rows) == 1
    row = rows[0]
    assert row["course_code"].startswith("TOV1") and row["enrolled_count"] == 2 and row["section_label"] == "A"
    assert row["classes_total"] >= 3 and row["classes_held"] == 2 and row["topics_covered"] == 1
    assert row["average_attendance"] == 75.0
    assert row["unmarked_past_classes"] == row["classes_total"] - 2          # the cancelled class is not counted as missing
    assert row["unmarked_past_classes"] >= 1


async def test_teacher_overview_is_private_to_the_teacher(app_client, admin_user, db_session):
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="TOV2", n_students=1)
    await _login(app_client, studs[0][1], "StudentPass123!")
    assert (await app_client.get("/api/v1/academics/offerings/me/overview")).status_code == 403          # a student has no teaching courses
    await _login_admin(app_client)
    assert (await app_client.get("/api/v1/academics/offerings/me/overview")).status_code == 403          # nor does the admin account