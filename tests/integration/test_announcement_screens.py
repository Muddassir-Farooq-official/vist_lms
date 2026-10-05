"""Announcements are reachable from every portal; the complaint dashboard is open to everyone who can file."""
from __future__ import annotations

import pytest

from app.main import ACCOUNTS_NAV_ITEMS, ADMIN_NAV_ITEMS, FACULTY_NAV_ITEMS, LIBRARY_NAV_ITEMS, STUDENT_NAV_ITEMS
from tests.integration.test_complaints_anonymous_and_charts import ADMIN, _become, _file, _login, _ready_student

pytestmark = pytest.mark.asyncio

PORTALS = {
    "student": STUDENT_NAV_ITEMS, "faculty": FACULTY_NAV_ITEMS, "library": LIBRARY_NAV_ITEMS, "accounts": ACCOUNTS_NAV_ITEMS, "admin": ADMIN_NAV_ITEMS,
}


@pytest.fixture(autouse=True)
def _no_login_throttle(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def test_every_portal_has_an_announcements_menu_entry_and_page(app_client):
    for portal, nav in PORTALS.items():
        entry = next((i for i in nav if i["slug"] == "announcements"), None) if "slug" in nav[0] else next((i for i in nav if i["href"].endswith("/announcements")), None)
        assert entry is not None and entry["href"] == f"/dashboard/{portal}/announcements", portal
        page = await app_client.get(entry["href"])
        assert page.status_code == 200 and "Announcements" in page.text, portal
    for portal in ("student", "faculty", "library", "accounts"):
        html = (await app_client.get(f"/dashboard/{portal}/announcements")).text
        for needle in ('id="ann-list"', 'id="ann-search"', 'id="ann-modal"', "announcements-feed.js", "announcement-ui.js", 'id="ann-readall"'):
            assert needle in html, f"{portal}: {needle}"


async def test_the_admin_screen_has_compose_attachments_and_officials(app_client):
    html = (await app_client.get("/dashboard/admin/announcements")).text
    for needle in ('data-tab="all"', 'data-tab="compose"', 'data-tab="officials"', 'id="ann-issuer"', 'id="ann-aud-students"', 'id="ann-aud-teachers"',
                   'id="ann-aud-staff"', 'id="ann-programs"', 'id="ann-files"', 'id="ann-pinned"', 'id="ann-expires"', 'id="ann-publish"', 'id="ann-draft"',
                   'id="auth-form"', "announcements-admin.js"):
        assert needle in html, needle
    js = (await app_client.get("/static/js/announcements-admin.js")).text
    for needle in ("/attachments", "Saved as a draft, but", "Uploading file", "Take down", "have opened it"):
        assert needle in js, needle
    feed = (await app_client.get("/static/js/announcements-feed.js")).text
    assert "?open=" not in feed and 'get("open")' in feed and "/read" in feed


async def test_student_and_teacher_home_show_the_latest_announcements(app_client):
    for portal in ("student", "faculty"):
        html = (await app_client.get(f"/dashboard/{portal}")).text
        assert 'id="sh-news"' in html and "has-news" in html and f"/dashboard/{portal}/announcements" in html, portal
    js = (await app_client.get("/static/js/portal-home.js")).text
    assert "loadNews(" in js and "announcements?limit=3" in js
    common = (await app_client.get("/static/js/admin-common.js")).text
    assert "wireAnnouncementBadge" in common and "announcements/unread-count" in common and "ANNOUNCEMENT:" in common


async def test_the_complaint_dashboard_is_open_to_students_and_teachers(app_client):
    for portal in ("student", "faculty"):
        html = (await app_client.get(f"/dashboard/{portal}/complaints")).text
        for needle in ('data-tab="overview"', 'id="co-chart-status"', 'id="co-chart-reason"', 'id="co-chart-weekly"', 'id="co-mine-total"',
                       'id="complaint-detail-modal"', 'id="cd-timeline"', "charts.js", "no names"):
            assert needle in html, f"{portal}: {needle}"
    js = (await app_client.get("/static/js/complaints-portal.js")).text
    assert "complaints/stats" in js and "openDetail" in js and "Waiting for the administrator" in js


async def test_a_complaint_remembers_when_review_began_and_the_filer_can_see_it(app_client, admin_user, db_session):
    await _login(app_client, **ADMIN)
    filer, target = await _ready_student(app_client, 21), await _ready_student(app_client, 22)
    await _become(app_client, filer)
    c = await _file(app_client, target["id"])
    assert c["under_review_at"] is None and c["resolved_at"] is None
    await _login(app_client, **ADMIN)
    assert (await app_client.post(f"/api/v1/complaints/{c['id']}/status", json={"status": "UNDER_REVIEW", "note": "Looking into it"})).status_code == 200
    first = (await app_client.get(f"/api/v1/complaints/{c['id']}")).json()["under_review_at"]
    assert first is not None
    assert (await app_client.post(f"/api/v1/complaints/{c['id']}/status", json={"status": "RESOLVED", "note": "Sorted"})).status_code == 200
    await _login(app_client, filer["username"], filer["password"])
    mine = (await app_client.get("/api/v1/complaints/me")).json()[0]
    assert mine["under_review_at"] == first and mine["resolved_at"] is not None and mine["status"] == "RESOLVED"      # the filer sees the whole timeline
    stats = (await app_client.get("/api/v1/complaints/stats")).json()
    assert stats["kpis"]["resolved"] == 1 and stats["kpis"]["total"] == 1 and stats["resolution_rate"] == 100.0