"""The assignment screens exist for the right people and are wired into Home, the menu and the bell."""
from __future__ import annotations

import pytest

from app.main import FACULTY_NAV_ITEMS, STUDENT_NAV_ITEMS

pytestmark = pytest.mark.asyncio


async def test_students_and_teachers_have_an_assignments_page_in_their_menu(app_client):
    for portal, nav in (("student", STUDENT_NAV_ITEMS), ("faculty", FACULTY_NAV_ITEMS)):
        entry = next(i for i in nav if i["slug"] == "assignments")
        assert entry["href"] == f"/dashboard/{portal}/assignments"
        page = await app_client.get(entry["href"])
        assert page.status_code == 200 and "Assignments" in page.text


async def test_the_student_page_has_the_three_lists_and_the_hand_in_form(app_client):
    html = (await app_client.get("/dashboard/student/assignments")).text
    for needle in ('data-tab="todo"', 'data-tab="done"', 'data-tab="closed"', 'id="asg-url"', 'id="asg-note"', 'id="asg-submit"', 'id="asg-private"', 'id="asg-anyway"',
                   "Anyone with the link", "Viewer", "student-assignments.js"):
        assert needle in html, needle
    js = (await app_client.get("/static/js/student-assignments.js")).text
    for needle in ("DRIVE_LINK_PRIVATE", "acknowledge_private", "/submit", "Submissions are closed" if False else "can no longer be changed", "closes in", 'get("open")'):
        assert needle in js, needle


async def test_the_teacher_page_sets_work_and_reviews_it(app_client):
    html = (await app_client.get("/dashboard/faculty/assignments")).text
    for needle in ('data-tab="list"', 'data-tab="review"', 'data-tab="new"', 'id="as-course"', 'id="as-title"', 'id="as-instructions"', 'id="as-deadline"', 'id="as-create"',
                   'id="rj-reason"', 'id="ed-deadline"', "faculty-assignments.js", "view access"):
        assert needle in html, needle
    js = (await app_client.get("/static/js/faculty-assignments.js")).text
    for needle in ("/review", "/close", 'rel="noopener noreferrer"', "Say why", "offerings/me/overview", "firstLoad"):
        assert needle in js, needle


async def test_assignments_show_up_on_home_and_in_the_bell(app_client):
    home = (await app_client.get("/static/js/portal-home.js")).text
    assert "assignments/mine" in home and "Assignment due" in home and "assignments/teaching" in home and "waiting for your review" in home
    assert "/dashboard/student/assignments" in (await app_client.get("/dashboard/student")).text
    assert "/dashboard/faculty/assignments" in (await app_client.get("/dashboard/faculty")).text
    assert "ASSIGNMENT:" in (await app_client.get("/static/js/admin-common.js")).text