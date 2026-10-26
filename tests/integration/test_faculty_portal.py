"""The faculty portal has the same dashboard design as the student one -- and no page can depend on a script it doesn't load."""
from __future__ import annotations

import re

import pytest

from app.main import ACCOUNTS_NAV_ITEMS, ADMIN_NAV_ITEMS, FACULTY_NAV_ITEMS, LIBRARY_NAV_ITEMS, STUDENT_NAV_ITEMS

pytestmark = pytest.mark.asyncio


async def test_every_page_loads_the_shared_ui_file_if_any_of_its_scripts_use_it(app_client):
    """Regression: the teacher's Fines page broke because its script needed window.StudentUI and the page never loaded it."""
    paths = {i["href"] for nav in (ADMIN_NAV_ITEMS, LIBRARY_NAV_ITEMS, ACCOUNTS_NAV_ITEMS, STUDENT_NAV_ITEMS, FACULTY_NAV_ITEMS) for i in nav}
    paths |= {"/dashboard/faculty/profile", "/dashboard/student/profile", "/dashboard/faculty/complaints", "/dashboard/student/complaints"}
    checked = 0
    for path in sorted(paths):
        html = (await app_client.get(path)).text
        for src in re.findall(r'<script src="(/static/js/[^"]+\.js)"', html):
            js = (await app_client.get(src)).text
            if "window.StudentUI" in js and src != "/static/js/student-ui.js":
                assert "/static/js/student-ui.js" in html, f"{path} loads {src}, which needs student-ui.js, but never loads it"
                checked += 1
    assert checked >= 8                      # the guard really examined the pages that use the shared file


async def test_faculty_home_is_a_dashboard_of_cards(app_client):
    html = (await app_client.get("/dashboard/faculty")).text
    for needle in ("kpi-row", 'id="today-list"', 'id="next-list"', 'id="week-strip"', 'id="attn-list"', 'id="course-cards"', "dash-actions", "portal-footer",
                   "data-card-tables", "faculty-home.js", "dash-common.js", "VIST LMS Faculty", "Needs your attention", "My courses"):
        assert needle in html, needle
    js = (await app_client.get("/static/js/faculty-home.js")).text
    for needle in ("offerings/me/overview", "offerings/me/schedule", "unmarked_past_classes", "Take attendance", "/results/offerings/"):
        assert needle in js, needle
    shared = (await app_client.get("/static/js/student-ui.js")).text
    assert "teacherClassCard" in shared and "window.PortalUI = window.StudentUI" in shared and "Take attendance" in shared


async def test_faculty_pages_use_cards_and_link_into_each_other(app_client):
    assert "my-offerings-grid" in (await app_client.get("/dashboard/faculty/courses")).text
    fines = (await app_client.get("/dashboard/faculty/fines")).text
    assert "my-fines-grid" in fines and "fine-stat-pending" in fines and "student-ui.js" in fines
    tt = (await app_client.get("/dashboard/faculty/timetable")).text
    assert "upcoming-grid" in tt and "add-all-calendar" in tt
    att = (await app_client.get("/dashboard/faculty/attendance")).text
    assert "my-requests-list" in att and 'id="roster-table"' in att and 'id="save-attendance-btn"' in att       # the register itself is untouched
    prof = (await app_client.get("/dashboard/faculty/profile")).text
    assert "profile-hero" in prof and "ph-avatar" in prof
    attendance_js = (await app_client.get("/static/js/faculty-attendance.js")).text
    assert "applyDeepLink" in attendance_js and 'params.get("session")' in attendance_js
    assert 'get("offering")' in (await app_client.get("/static/js/faculty-courses.js")).text