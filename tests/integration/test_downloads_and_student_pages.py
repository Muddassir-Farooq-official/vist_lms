"""PDF and calendar downloads must either work or explain themselves; student pages ship their cards."""
from __future__ import annotations

import pytest

from app.academics.calendar_export import _fold, build_calendar
from tests.integration.test_attendance_and_grading import (
    _create_n_sessions,
    _login,
    _login_admin,
    _setup_offering_with_enrolled_students,
)
from tests.integration.test_module3_sections_allocation import _admin, _course, _offering, _period, _slot

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _no_login_throttle(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def test_pdf_still_works_when_dozens_of_classes_share_one_time_slot(app_client, admin_user, db_session):
    """Regression: 25+ classes in one slot made the PDF generator crash, and the browser saved the error as a 'PDF'."""
    await _admin(app_client)
    period = await _period(app_client, "09:00:00", "10:00:00")
    for i in range(30):
        oid = await _offering(app_client, await _course(app_client, f"CRW-{i:03d}"))
        assert (await _slot(app_client, oid, period, f"R-{i}")).status_code == 201     # different rooms: no clash, all at 09:00 Monday
    r = await app_client.get("/api/v1/academics/timetable/weekly.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF-")


async def test_a_pdf_that_cannot_be_made_says_why_instead_of_returning_a_broken_file(app_client, admin_user, db_session, monkeypatch):
    import app.academics.router as router_module

    await _admin(app_client)

    def missing_library(_data):
        raise ImportError("No module named 'reportlab'")

    monkeypatch.setattr(router_module, "render_pdf", missing_library)
    r = await app_client.get("/api/v1/academics/timetable/weekly.pdf")
    assert r.status_code == 503 and r.headers["content-type"].startswith("application/json")
    assert r.json()["error"]["code"] == "PDF_UNAVAILABLE" and "pip install reportlab" in r.json()["error"]["message"]

    def broken(_data):
        raise RuntimeError("boom")

    monkeypatch.setattr(router_module, "render_pdf", broken)
    r = await app_client.get("/api/v1/academics/timetable/weekly.pdf")
    assert r.status_code == 500 and r.json()["error"]["code"] == "PDF_FAILED" and "could not be created" in r.json()["error"]["message"]


async def test_health_says_whether_pdf_support_is_installed(app_client):
    body = (await app_client.get("/health/ready")).json()
    assert body["pdf_support"] == "installed"


async def test_calendar_files_for_one_class_and_for_the_whole_timetable(app_client, admin_user, db_session):
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="ICS1", n_students=1)
    sessions = await _create_n_sessions(app_client, offering_id, 3)
    await _login(app_client, studs[0][1], "StudentPass123!")

    one = await app_client.get(f"/api/v1/academics/class-sessions/{sessions[0]}/calendar.ics")
    assert one.status_code == 200 and one.headers["content-type"].startswith("text/calendar")
    assert 'attachment; filename="ICS1' in one.headers["content-disposition"] and one.headers["content-disposition"].endswith('.ics"')
    text = one.content.decode("utf-8")
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n") and text.count("BEGIN:VEVENT") == 1
    assert "\n" not in text.replace("\r\n", "") and "SUMMARY:ICS1" in text and "TRIGGER:-PT15M" in text
    import re

    assert re.search(r"DTSTART:\d{8}T\d{6}Z", text) and re.search(r"DTEND:\d{8}T\d{6}Z", text)     # UTC: every calendar app agrees

    everything = await app_client.get("/api/v1/academics/timetable/calendar.ics")
    assert everything.status_code == 200 and 'filename="my-timetable.ics"' in everything.headers["content-disposition"]
    assert everything.text.startswith("BEGIN:VCALENDAR") and everything.text.rstrip().endswith("END:VCALENDAR")
    assert everything.text.count("BEGIN:VEVENT") == 0            # only UPCOMING classes are added; these seeded ones are in the past


async def test_nobody_gets_a_calendar_file_for_a_class_that_is_not_theirs(app_client, admin_user, db_session):
    offering_id, _studs = await _setup_offering_with_enrolled_students(app_client, code="ICS2", n_students=1)
    sessions = await _create_n_sessions(app_client, offering_id, 1)
    made = await app_client.post("/api/v1/students", json={
        "first_name": "Other", "last_name": "Student", "date_of_birth": "2005-03-03", "gender": "Female", "cnic": "35202-4444444-4",
        "contact_no": "03001110000", "marital_status": "Single", "religion": "Islam", "nationality": "Pakistani", "guardian_name": "G",
        "guardian_cnic": "35202-5555555-5", "relation_with_guardian": "Father", "emergency_contact_no": "03001110001",
        "present_address": "x", "province": "Punjab", "district": "Lahore", "city": "Lahore"})
    assert made.status_code == 201, made.text
    await _login(app_client, made.json()["username"], "vertex123")
    assert (await app_client.post("/api/v1/auth/change-password", json={"current_password": "vertex123", "new_password": "Other#Pass2026x"})).status_code == 204
    assert (await app_client.get(f"/api/v1/academics/class-sessions/{sessions[0]}/calendar.ics")).status_code == 403   # not enrolled
    await _login_admin(app_client)
    assert (await app_client.get("/api/v1/academics/timetable/calendar.ics")).status_code == 403                     # admin has no personal timetable


async def test_calendar_times_are_converted_from_the_institutes_clock_to_utc():
    from datetime import date, time

    event = {"id": "abc", "course_code": "NUR-202", "course_title": "Nursing \u2014 Basics, Part 1; \"Lab\"", "session_date": date(2026, 10, 6),
             "start_time": time(8, 30), "end_time": time(10, 0), "room": "204", "teacher_name": "Talha Hussain", "title": "Intro"}
    text = build_calendar([event]).decode("utf-8")
    assert "DTSTART:20261006T033000Z" in text and "DTEND:20261006T050000Z" in text            # 08:30 in Karachi (UTC+5) = 03:30 UTC
    assert "Basics\\, Part 1\\; " in text                                                       # commas / semicolons are escaped
    assert all(len(line.encode("utf-8")) <= 75 for line in text.split("\r\n"))                  # long lines are folded
    long_line = _fold("DESCRIPTION:" + "very long text " * 40)
    assert all(len(part.encode("utf-8")) <= 75 for part in long_line.split("\r\n")) and "\r\n " in long_line


async def test_student_pages_ship_their_cards_in_the_existing_blue_theme(app_client):
    checks = {
        "/dashboard/student/courses": ("my-enrollments-grid", "course-grid", "sh-card"),
        "/dashboard/student/attendance": ("att-overall", "att-grid", "history-list"),
        "/dashboard/student/timetable": ("upcoming-grid", "add-all-calendar", 'data-download="timetable.pdf"'),
        "/dashboard/student/fines": ("my-fines-grid", "fine-stat-pending"),
        "/dashboard/student/profile": ("profile-hero", "ph-avatar", "ph-name"),
    }
    for path, needles in checks.items():
        html = (await app_client.get(path)).text
        assert "data-card-tables" in html and "student-ui.js" in html, path
        for needle in needles:
            assert needle in html, f"{path}: {needle}"
    css = (await app_client.get("/static/css/dashboard.css")).text
    for needle in (".course-card", ".att-row", ".fine-card", ".profile-hero", "body[data-card-tables]", ".toast"):
        assert needle in css, needle
    assert "--teal-500" not in css and "#0d9488" not in css            # no cyan: the app's own blue / yellow theme
    js = (await app_client.get("/static/js/admin-common.js")).text
    assert "downloadFile" in js and "a[data-download]" in js
    tt = (await app_client.get("/static/js/weekly-timetable.js")).text
    assert "Try again" in tt and "25000" in tt                            # a timetable can never load forever