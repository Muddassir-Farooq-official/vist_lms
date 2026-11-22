"""My shortcuts (app/navigation): each person's starred pages per portal, in order, at most 12, never another person's."""
from __future__ import annotations

import pytest

from app.core.config import settings
from tests.integration.test_attendance_and_grading import _login_admin
from tests.integration.test_fees import _create_accounts_officer

pytestmark = pytest.mark.asyncio
S = "/api/v1/nav/shortcuts"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def test_shortcuts_are_per_person_and_per_portal(app_client, admin_user, db_session):
    await _login_admin(app_client)
    assert (await app_client.get(S, params={"portal": "admin"})).json()["slugs"] == []
    r = await app_client.put(S, json={"portal": "admin", "slugs": ["fee-defaulters", "students", "fee-defaulters"]})
    assert r.status_code == 200 and r.json()["slugs"] == ["fee-defaulters", "students"]          # duplicates dropped, order kept
    assert (await app_client.get(S, params={"portal": "admin"})).json()["slugs"] == ["fee-defaulters", "students"]
    assert (await app_client.get(S, params={"portal": "accounts"})).json()["slugs"] == []          # another portal: its own list
    assert (await app_client.put(S, json={"portal": "admin", "slugs": ["students", "backups"]})).json()["slugs"] == ["students", "backups"]
    assert (await app_client.put(S, json={"portal": "admin", "slugs": [f"p{i}" for i in range(13)]})).status_code == 422
    assert (await app_client.put(S, json={"portal": "admin", "slugs": ["<script>"]})).status_code == 422
    assert (await app_client.get(S, params={"portal": "../x"})).status_code == 422
    page = (await app_client.get("/dashboard/admin/students")).text
    assert "wireShortcuts" not in page and "admin-common.js" in page                               # built by the shared script

    await _create_accounts_officer(app_client, db_session, code="NAVSC")                            # someone else: empty, separate
    assert (await app_client.get(S, params={"portal": "admin"})).json()["slugs"] == []
    assert (await app_client.put(S, json={"portal": "accounts", "slugs": ["fees"]})).json()["slugs"] == ["fees"]
    await _login_admin(app_client)
    assert (await app_client.get(S, params={"portal": "admin"})).json()["slugs"] == ["students", "backups"]


async def test_shortcuts_need_a_login(app_client):
    assert (await app_client.get(S, params={"portal": "admin"})).status_code == 401
