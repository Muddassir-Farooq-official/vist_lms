"""Navigation: grouped menus, phone bottom bar, real Home pages, no dead links."""
from __future__ import annotations

import re

import pytest

from app.auth.redirects import PORTAL_BY_ROLE
from app.main import ACCOUNTS_NAV_ITEMS, ADMIN_NAV_ITEMS, EXAM_NAV_ITEMS, FACULTY_NAV_ITEMS, GATE_NAV_ITEMS, HOD_NAV_ITEMS, LIBRARY_NAV_ITEMS, PARENT_NAV_ITEMS, PRINCIPAL_NAV_ITEMS, STUDENT_NAV_ITEMS, WARDEN_NAV_ITEMS

pytestmark = pytest.mark.asyncio

ALL_NAVS = {
    "admin": ADMIN_NAV_ITEMS, "library": LIBRARY_NAV_ITEMS, "accounts": ACCOUNTS_NAV_ITEMS,
    "student": STUDENT_NAV_ITEMS, "faculty": FACULTY_NAV_ITEMS, "gate": GATE_NAV_ITEMS, "exam": EXAM_NAV_ITEMS, "warden": WARDEN_NAV_ITEMS,
}


@pytest.mark.parametrize("portal", list(ALL_NAVS))
async def test_every_menu_link_opens_a_real_page(app_client, portal):
    """A menu entry that 404s is the worst kind of navigation bug."""
    for item in ALL_NAVS[portal]:
        r = await app_client.get(item["href"])
        assert r.status_code == 200, f"{portal}: '{item['label']}' -> {item['href']} returned {r.status_code}"


@pytest.mark.parametrize("portal", list(ALL_NAVS))
async def test_menu_items_are_unique_and_phone_bar_is_small(portal):
    items = ALL_NAVS[portal]
    assert len({i["href"] for i in items}) == len(items), "duplicate menu destinations"
    assert len({i["slug"] for i in items}) == len(items), "duplicate menu slugs"
    assert sum(1 for i in items if i["mobile"]) <= 4, "phone bar holds at most 4 + More"
    assert not [i for i in items if not i["enabled"]], "no 'coming soon' entries in menus"


async def test_every_sign_in_destination_is_in_that_portals_menu():
    menus = {"/dashboard/admin": ADMIN_NAV_ITEMS, "/dashboard/accounts/fees": ACCOUNTS_NAV_ITEMS,
             "/dashboard/library": LIBRARY_NAV_ITEMS, "/dashboard/faculty": FACULTY_NAV_ITEMS,
             "/dashboard/student": STUDENT_NAV_ITEMS, "/dashboard/gate": GATE_NAV_ITEMS, "/dashboard/exam": EXAM_NAV_ITEMS, "/dashboard/warden": WARDEN_NAV_ITEMS,
             "/dashboard/hod": HOD_NAV_ITEMS, "/dashboard/principal": PRINCIPAL_NAV_ITEMS, "/dashboard/parent": PARENT_NAV_ITEMS}
    for _role, path in PORTAL_BY_ROLE:
        assert path in {i["href"] for i in menus[path]}, f"{path} is a landing page but not in its own menu"


async def test_menu_is_grouped_and_shell_has_phone_bar_and_no_api_docs_button(app_client):
    html = (await app_client.get("/dashboard/admin/students")).text
    for heading in ("People", "Academics", "Finance", "System"):
        assert f'class="drawer-group" role="presentation">{heading}<' in re.sub(r"\s+", " ", html).replace("> ", ">").replace(" <", "<") or heading in html
    assert 'class="bottom-nav"' in html
    assert len(re.findall(r'class="bottom-link', html)) == 5          # 4 quick links + More
    assert 'href="/docs"' not in html and "API Docs" not in html
    assert "Soon" not in html and "Hostel" not in html


async def test_student_and_teacher_home_are_real_pages_and_library_moved(app_client):
    student = (await app_client.get("/dashboard/student")).text
    assert 'data-home="student"' in student and "Your next classes" in student and "portal-home.js" in student
    assert "library-portal.js" not in student                          # Library is no longer the home page
    faculty = (await app_client.get("/dashboard/faculty")).text
    assert 'data-home="faculty"' in faculty and "Today" in faculty
    for path in ("/dashboard/student/library", "/dashboard/faculty/library"):
        r = await app_client.get(path)
        assert r.status_code == 200 and "library-portal.js" in r.text


async def test_admin_can_get_back_from_library_and_accounts_portals(app_client):
    for path in ("/dashboard/library", "/dashboard/accounts/fees"):
        html = (await app_client.get(path)).text
        assert 'id="back-to-admin"' in html and "hidden" in html.split('id="back-to-admin"')[1][:120]


async def test_attendance_screens_have_no_late_or_leave_and_use_a_two_way_switch(app_client):
    """Attendance is Present or Absent only. Guards the screens and scripts against the concepts returning."""
    import re

    for path in ("/dashboard/faculty/attendance", "/dashboard/admin/attendance", "/dashboard/student/attendance"):
        html = (await app_client.get(path)).text
        assert not re.search(r"\b(late|leave)\b", re.sub(r"late[-_ ]?(marking|note)|late note", "", html, flags=re.I), re.I), path
    for js in ("attendance-common.js", "student-attendance.js", "admin-attendance.js", "faculty-attendance.js"):
        text = (await app_client.get(f"/static/js/{js}")).text
        text = re.sub(r"late[_ -]?(marking|note)|lateNote|late note", "", text, flags=re.I)
        assert not re.search(r"\b(LATE|LEAVE|late_student_ids|leave_student_ids)\b", text), js
    assert "seg-present" in (await app_client.get("/static/js/attendance-common.js")).text


async def test_mobile_layer_is_shipped(app_client):
    css = (await app_client.get("/static/css/dashboard.css")).text
    for needle in ("dialog.modal", "stackable", ".seg-opt", ".form-section", ".bottom-nav", ".portal-tabs.has-more"):
        assert needle in css, needle
    js = (await app_client.get("/static/js/admin-common.js")).text
    assert "enhanceTables" in js and "enhanceTabs" in js and "watchDialogMessages" in js


async def test_teacher_can_manage_content_and_admin_has_one_click_timetable_tools(app_client):
    faculty = (await app_client.get("/dashboard/faculty/courses")).text
    assert "not edit" not in faculty and "set by admin" not in faculty          # the old "view only" wording is gone
    for needle in ('id="syllabus-file"', 'id="plan-paste"', 'id="plan-apply"', 'id="content-success"'):
        assert needle in faculty, needle
    admin = (await app_client.get("/dashboard/admin/courses")).text
    for needle in ('id="open-auto-schedule"', 'id="open-copy-timetable"', 'id="auto-schedule-modal"', 'id="copy-timetable-modal"'):
        assert needle in admin, needle
    js = (await app_client.get("/static/js/faculty-courses.js")).text
    assert "lecture-plan" in js and "resources/link" in js


async def test_covered_topics_and_content_locks_are_visible_on_every_screen(app_client):
    admin = (await app_client.get("/dashboard/admin/courses/offering/00000000-0000-0000-0000-000000000000")).text
    for needle in ('id="lock-past-btn"', 'id="lock-course-btn"', 'id="unlock-all-btn"', 'id="lock-status"', 'id="coverage-summary"'):
        assert needle in admin, needle
    faculty = (await app_client.get("/dashboard/faculty/courses")).text
    assert 'id="lock-banner"' in faculty and 'id="plan-summary"' in faculty
    for name, needles in {
        "faculty-courses.js": ("topic-completed", "plan-done", "badge-locked", "Mark this topic as covered"),
        "student-courses.js": ("plan-done", "badge-covered", "topics covered so far"),
        "admin-offering-detail.js": ("content-lock", "badge-covered", "PAST_CLASSES", "plan-done"),
    }.items():
        js = (await app_client.get(f"/static/js/{name}")).text
        for needle in needles:
            assert needle in js, f"{name}: {needle}"
    css = (await app_client.get("/static/css/dashboard.css")).text
    assert ".plan-done" in css and ".badge-covered" in css and ".lock-bar" in css


async def test_weekly_timetable_sheet_is_on_the_admin_teacher_and_student_pages(app_client):
    for path in ("/dashboard/admin/timetable", "/dashboard/faculty/timetable", "/dashboard/student/timetable"):
        html = (await app_client.get(path)).text
        for needle in ('id="weekly-timetable"', 'id="tt-pdf"', 'id="tt-grid"', "data-auto-tabs", "weekly-timetable.js", 'id="tt-term"'):
            assert needle in html, f"{path}: {needle}"
    css = (await app_client.get("/static/css/dashboard.css")).text
    for needle in (".tt-grid", ".tt-entry", ".tt-daytab", ".tt-filter-box"):
        assert needle in css, needle
    js = (await app_client.get("/static/js/weekly-timetable.js")).text
    assert "/api/v1/academics/timetable" in js and "/weekly.pdf" in js and "/filters" in js


async def test_timetable_page_explains_empty_semesters(app_client):
    js = (await app_client.get("/static/js/weekly-timetable.js")).text
    for needle in ("semester_counts", "Nothing is scheduled for Semester", "tt-jump", "placements"):
        assert needle in js, needle