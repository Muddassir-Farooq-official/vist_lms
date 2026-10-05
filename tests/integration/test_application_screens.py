"""Applications are reachable from every portal and wired into the menu, Home and the bell."""
from __future__ import annotations

import pytest

from app.main import ACCOUNTS_NAV_ITEMS, ADMIN_NAV_ITEMS, FACULTY_NAV_ITEMS, LIBRARY_NAV_ITEMS, STUDENT_NAV_ITEMS

pytestmark = pytest.mark.asyncio

PORTALS = {"student": STUDENT_NAV_ITEMS, "faculty": FACULTY_NAV_ITEMS, "library": LIBRARY_NAV_ITEMS, "accounts": ACCOUNTS_NAV_ITEMS, "admin": ADMIN_NAV_ITEMS}


async def test_every_portal_has_an_applications_menu_entry_and_page(app_client):
    for portal, nav in PORTALS.items():
        entry = next(i for i in nav if i["slug"] == "applications")
        assert entry["href"] == f"/dashboard/{portal}/applications" and entry["icon"] == "file"
        page = await app_client.get(entry["href"])
        assert page.status_code == 200 and "Applications" in page.text, portal


async def test_everyone_but_the_admin_gets_the_apply_screen(app_client):
    for portal in ("student", "faculty", "library", "accounts"):
        html = (await app_client.get(f"/dashboard/{portal}/applications")).text
        for needle in ('data-tab="mine"', 'data-tab="new"', 'id="ap-type"', 'id="ap-from"', 'id="ap-to"', 'id="ap-subject"', 'id="ap-details"', 'id="ap-files"',
                       'id="ap-submit"', 'id="ap-modal"', 'id="ap-thread"', 'id="ap-reply"', 'id="ap-withdraw"', "applications-portal.js"):
            assert needle in html, f"{portal}: {needle}"
    js = (await app_client.get("/static/js/applications-portal.js")).text
    for needle in ("FormData", "/withdraw", "/attachments", "yearly allowance", 'get("open")', "applications:badge", "to answer"):
        assert needle in js, needle


async def test_the_admin_screen_has_the_queue_filters_overview_and_types(app_client):
    html = (await app_client.get("/dashboard/admin/applications")).text
    for needle in ('data-tab="queue"', 'data-tab="all"', 'data-tab="overview"', 'data-tab="types"', 'id="rv-modal"', 'data-act="START_REVIEW"', 'data-act="REQUEST_INFO"',
                   'data-act="APPROVE"', 'data-act="REJECT"', 'id="rv-note"', 'id="f-kind"', 'id="c-status"', 'id="c-week"', 'id="t-form"', "applications-admin.js", "charts.js"):
        assert needle in html, needle
    # the regression that bit once already: a page whose script uses the shared UI file must load it
    assert "/static/js/student-ui.js" in html
    js = (await app_client.get("/static/js/applications-admin.js")).text
    for needle in ("/review", "/overview", "Change to approved", "Give a reason for rejecting", "needs_action=true"):
        assert needle in js, needle


async def test_menu_badge_bell_label_and_home_alert_are_wired(app_client):
    common = (await app_client.get("/static/js/admin-common.js")).text
    assert "wireApplicationBadge" in common and "applications/badge" in common and "APPLICATION:" in common
    home = (await app_client.get("/static/js/portal-home.js")).text
    assert "applications/mine" in home and "needs a reply" in home