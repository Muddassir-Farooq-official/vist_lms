"""The gate screens exist for the right people and are wired to the right things."""
from __future__ import annotations

import pytest

from app.auth.redirects import home_for_roles
from app.main import ADMIN_NAV_ITEMS, GATE_NAV_ITEMS

pytestmark = pytest.mark.asyncio


async def test_a_security_guard_lands_on_the_gate_page():
    assert home_for_roles(["SECURITY_GUARD"]) == "/dashboard/gate"
    assert home_for_roles(["SECURITY_GUARD", "ADMIN"]) == "/dashboard/admin"                       # the most privileged portal wins, as for everyone else
    assert home_for_roles(["SECURITY_GUARD"], must_change_password=True) == "/change-password"


async def test_the_guards_menu_holds_only_what_a_guard_needs(app_client):
    slugs = [i["slug"] for i in GATE_NAV_ITEMS]
    assert slugs == ["gate", "log", "announcements", "tasks", "applications", "purchases", "payslips"]          # (a guard may also ask for something to be bought, and sees their own payslips)
    for item in GATE_NAV_ITEMS:
        assert (await app_client.get(item["href"])).status_code == 200, item["href"]
    html = (await app_client.get("/dashboard/gate")).text
    assert 'data-allowed-roles="ADMIN,SECURITY_GUARD"' in html and "VIST Gate" in html
    for forbidden in ("/dashboard/admin/students", "/dashboard/admin/fees", "/dashboard/admin/employees"):
        assert forbidden not in html


async def test_the_gate_page_has_search_filters_and_big_buttons(app_client):
    html = (await app_client.get("/dashboard/gate")).text
    for needle in ('id="g-q"', 'id="g-kind"', 'id="g-state"', 'data-state="LATE"', 'data-state="NOT_ARRIVED"', 'id="g-program"', 'id="g-semester"', 'id="g-role"', 'id="g-summary"', "gate-home.js", "student-ui.js"):
        assert needle in html, needle
    js = (await app_client.get("/static/js/gate-home.js")).text
    for needle in ("\"check-in\"", "\"check-out\"", "/undo", "LATE by", "Fine request sent to the administration", "setInterval(summary"):
        assert needle in js, needle
    log = (await app_client.get("/dashboard/gate/log")).text
    assert 'id="l-late"' in log and 'id="l-day"' in log and "gate-log.js" in log and "gate-ui.js" in log


async def test_the_administration_gets_the_five_gate_tabs(app_client):
    assert next(i for i in ADMIN_NAV_ITEMS if i["slug"] == "gate")["href"] == "/dashboard/admin/gate"
    html = (await app_client.get("/dashboard/admin/gate")).text
    for tab in ("log", "fines", "staff", "students", "report"):
        assert f'data-tab="{tab}"' in html, tab
    for needle in ('id="sd-form"', 'id="sr-form"', 'id="sr-rate"', 'id="sr-max"', 'id="sr-auto"', 'id="st-bulk-open"', 'id="gt-modal"', 'id="ge-modal"', 'id="rp-days"', "/static/js/student-ui.js", "gate-admin.js"):
        assert needle in html, needle
    js = (await app_client.get("/static/js/gate-admin.js")).text
    for needle in ("/fines/", "/decide", "/schedules/bulk", "/settings", "/report", "Do not charge", "example()"):
        assert needle in js, needle
    assert "GATE" in (await app_client.get("/static/js/my-fines.js")).text                       # the student sees why a fine was charged