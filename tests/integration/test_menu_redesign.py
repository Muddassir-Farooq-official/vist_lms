"""The menu (templates/_nav.html, app/school/nav.py nav_sections, app/core/nav_badges.py): job-based groups in every
office's portal, setup pages moved to Settings -> More tools but still found by the search, and waiting-work badges
sent only to people who can act on them."""
from __future__ import annotations

import re

import pytest

from app.approvals.models import ApprovalRequest
from app.core.config import settings
from app.school.nav import nav_sections
from tests.integration.test_attendance_and_grading import _login_admin
from tests.integration.test_fees import _create_accounts_officer

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


def test_sections_keep_each_group_together_in_first_seen_order():
    items = [{"slug": "a", "group": None}, {"slug": "b", "group": "X"}, {"slug": "c", "group": "Y"}, {"slug": "d", "group": "X"}, {"slug": "e", "group": None}]
    assert [(g, [i["slug"] for i in lst]) for g, lst in nav_sections(items)] == [(None, ["a", "e"]), ("X", ["b", "d"]), ("Y", ["c"])]


async def test_every_admin_page_is_still_reachable_and_setup_pages_are_under_settings(app_client, admin_user):
    from app.main import ADMIN_NAV_ITEMS

    await _login_admin(app_client)
    html = (await app_client.get("/dashboard/admin")).text
    for item in ADMIN_NAV_ITEMS:                                    # nothing dropped from the menu (setup pages: hidden, for the search)
        assert f'data-nav="{item["slug"]}"' in html, item["slug"]
    hub = [i for i in ADMIN_NAV_ITEMS if i["hub"]]
    assert {i["slug"] for i in hub} >= {"backups", "server-health", "imports", "activity"}
    page = (await app_client.get("/dashboard/admin/settings")).text
    assert "More tools" in page and all(f'href="{i["href"]}"' in page for i in hub)
    on_it = (await app_client.get("/dashboard/admin/backups")).text           # the open setup page shows in the menu
    tag = re.search(r'<a [^>]*data-nav="backups"[^>]*>', on_it).group(0)
    assert "hidden" not in tag and 'aria-current="page"' in tag


async def test_other_portals_are_grouped_too(app_client, admin_user):
    await _login_admin(app_client)
    accounts = (await app_client.get("/dashboard/accounts/fees")).text
    for g in ("Fees", "Accounts &amp; store", "Reports", "Me"):
        assert f'data-group="{g}"' in accounts, g
    exam = (await app_client.get("/dashboard/exam/home")).text
    assert 'data-group="Exams"' in exam and 'data-badge="result_sheets"' in exam and 'id="nav-search"' in exam


async def test_badges_count_waiting_work_for_people_who_can_act(app_client, admin_user, db_session):
    await _login_admin(app_client)
    before = (await app_client.get("/api/v1/nav/badges")).json()
    assert set(before) == {"approvals", "profile_changes", "certificates", "result_sheets"}
    assert all(isinstance(v, int) for v in before.values())
    db_session.add(ApprovalRequest(kind="FINE_WAIVE", summary="Waive a test fine", payload={}, status="REQUESTED"))
    db_session.add(ApprovalRequest(kind="FINE_WAIVE", summary="Already decided", payload={}, status="APPROVED"))
    await db_session.flush()
    after = (await app_client.get("/api/v1/nav/badges")).json()
    assert after["approvals"] == before["approvals"] + 1
    # the accounts office cannot decide approvals or results: those counts are not sent to it
    await _create_accounts_officer(app_client, db_session, code="NAVACC")
    mine = (await app_client.get("/api/v1/nav/badges")).json()
    assert "approvals" not in mine and "result_sheets" not in mine
