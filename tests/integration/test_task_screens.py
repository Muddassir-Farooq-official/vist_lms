"""The task screens exist for staff (not students), and the administration gets the extra tracking tabs."""
from __future__ import annotations

import pytest

from app.main import ACCOUNTS_NAV_ITEMS, ADMIN_NAV_ITEMS, FACULTY_NAV_ITEMS, LIBRARY_NAV_ITEMS, STUDENT_NAV_ITEMS

pytestmark = pytest.mark.asyncio

STAFF = {"faculty": FACULTY_NAV_ITEMS, "library": LIBRARY_NAV_ITEMS, "accounts": ACCOUNTS_NAV_ITEMS, "admin": ADMIN_NAV_ITEMS}


async def test_every_staff_portal_has_a_tasks_page_and_students_do_not(app_client):
    for portal, nav in STAFF.items():
        entry = next(i for i in nav if i["slug"] == "tasks")
        assert entry["href"] == f"/dashboard/{portal}/tasks" and entry["icon"] == "tasks"
        assert (await app_client.get(entry["href"])).status_code == 200, portal
    assert not any(i["slug"] == "tasks" for i in STUDENT_NAV_ITEMS)
    assert (await app_client.get("/dashboard/student/tasks")).status_code == 404


async def test_staff_get_the_four_tabs_and_the_administration_gets_three_more(app_client):
    for portal in ("faculty", "library", "accounts"):
        html = (await app_client.get(f"/dashboard/{portal}/tasks")).text
        for tab in ("inbox", "sent", "new", "mine"):
            assert f'data-tab="{tab}"' in html, f"{portal}: {tab}"
        assert 'data-tab="flags"' not in html and 'data-tab="staff"' not in html and "tk-staff-body" not in html, portal
        for needle in ('id="tk-assignee"', 'id="tk-deadline"', 'id="tk-priority"', 'id="tk-create"', 'id="tk-act"', 'id="tk-modal"', "At least 3 hours", "/static/js/student-ui.js", "tasks-portal.js"):
            assert needle in html, f"{portal}: {needle}"
    admin = (await app_client.get("/dashboard/admin/tasks")).text
    for needle in ('data-tab="flags"', 'data-tab="staff"', 'data-tab="all"', 'id="tk-staff-body"', 'id="al-overdue"', 'id="al-days"', 'id="tk-person"', "Last 30"[:0] + "How are these numbers worked out?"):
        assert needle in admin, needle


async def test_the_screen_logic_enforces_what_the_server_enforces(app_client):
    js = (await app_client.get("/static/js/tasks-portal.js")).text
    for needle in ("MIN_HOURS = 3", "at least \" + MIN_HOURS + \" hours", "/respond", "/complete", "/cancel", "/flag", "/resolve", "reports/staff", "reports/person/", "reports/me",
                   "Explain why (at least a sentence)", "Give it to someone else", "follow_up_of_id", "tasks:badge", 'get("open")', "Last 30 days", "All time"):
        assert needle in js, needle
    common = (await app_client.get("/static/js/admin-common.js")).text
    assert "wireTaskBadge" in common and "tasks/summary" in common and "TASK:" in common
    assert "tasks/summary" in (await app_client.get("/static/js/portal-home.js")).text