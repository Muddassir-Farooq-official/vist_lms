"""Notification bell: paging, privacy, and links that always lead somewhere real."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.notifications.models import Notification, NotificationCategory
from app.notifications.service import create_notification
from tests.integration.test_module3_sections_allocation import _admin

pytestmark = pytest.mark.asyncio


async def _add(db, user_id, n, **kw):
    """n notifications, oldest first, one minute apart."""
    base = datetime.now(timezone.utc) - timedelta(minutes=n)
    for i in range(n):
        note = await create_notification(db, user_id=user_id, category=kw.get("category", NotificationCategory.GENERAL),
                                         title=f"Note {i}", message=f"Message {i}", link=kw.get("link"))
        note.created_at = base + timedelta(minutes=i)
    await db.commit()


async def test_list_is_newest_first_and_can_be_paged(app_client, admin_user, db_session):
    await _add(db_session, admin_user.id, 5)
    await _admin(app_client)
    first = (await app_client.get("/api/v1/notifications/me", params={"limit": 2, "offset": 0})).json()
    second = (await app_client.get("/api/v1/notifications/me", params={"limit": 2, "offset": 2})).json()
    assert [n["title"] for n in first] == ["Note 4", "Note 3"] and [n["title"] for n in second] == ["Note 2", "Note 1"]
    assert not {n["id"] for n in first} & {n["id"] for n in second}
    assert len((await app_client.get("/api/v1/notifications/me", params={"limit": 9999})).json()) == 5     # limit is capped, not trusted
    assert len((await app_client.get("/api/v1/notifications/me", params={"limit": 0})).json()) == 1        # never zero / negative


async def test_unread_count_and_marking_read(app_client, admin_user, db_session):
    await _add(db_session, admin_user.id, 3)
    await _admin(app_client)
    assert (await app_client.get("/api/v1/notifications/me/unread-count")).json() == {"unread_count": 3}
    items = (await app_client.get("/api/v1/notifications/me")).json()
    assert (await app_client.post(f"/api/v1/notifications/{items[0]['id']}/read")).json()["is_read"] is True
    assert (await app_client.get("/api/v1/notifications/me/unread-count")).json()["unread_count"] == 2
    assert len((await app_client.get("/api/v1/notifications/me", params={"unread_only": True})).json()) == 2
    assert (await app_client.post("/api/v1/notifications/me/read-all")).json() == {"unread_count": 0}
    assert (await app_client.get("/api/v1/notifications/me/unread-count")).json()["unread_count"] == 0


async def test_nobody_can_read_or_touch_someone_elses_notifications(app_client, admin_user, db_session):
    from app.auth.models import User
    from app.core.security import hash_password

    other = User(username="someone_else", password_hash=hash_password("Whatever#12345"), must_change_password=False)
    db_session.add(other)
    await db_session.commit()
    await _add(db_session, other.id, 2)
    await _add(db_session, admin_user.id, 1)
    await _admin(app_client)
    mine = (await app_client.get("/api/v1/notifications/me")).json()
    assert len(mine) == 1                                                       # only my own
    theirs = (await db_session.execute(__import__("sqlalchemy").select(Notification).where(Notification.user_id == other.id))).scalars().first()
    assert (await app_client.post(f"/api/v1/notifications/{theirs.id}/read")).status_code == 404
    await app_client.post("/api/v1/notifications/me/read-all")
    await db_session.refresh(theirs)
    assert theirs.is_read is False                                              # read-all never reaches other people's


async def test_a_notification_never_links_to_a_page_that_does_not_exist(app_client, admin_user, db_session):
    good = await create_notification(db_session, user_id=admin_user.id, category=NotificationCategory.FINE,
                                     title="ok", message="ok", link="/dashboard/student/fines")
    dead = await create_notification(db_session, user_id=admin_user.id, category=NotificationCategory.FEE,
                                     title="dead", message="dead", link="/dashboard/student/fees-that-do-not-exist")
    none = await create_notification(db_session, user_id=admin_user.id, category=NotificationCategory.GENERAL,
                                     title="none", message="none", link=None)
    assert good.link == "/dashboard/student/fines" and dead.link is None and none.link is None


async def test_every_dashboard_link_written_in_the_code_is_a_real_page(app_client):
    """Scans the source: any "/dashboard/..." path used outside the menu definitions must exist."""
    from app.main import app

    pages = {r.path for r in app.routes if getattr(r, "path", None) and "GET" in getattr(r, "methods", set())}
    root = Path(__file__).resolve().parents[2] / "app"
    bad = []
    for f in root.rglob("*.py"):
        if f.name == "main.py":
            continue
        for path in set(re.findall(r'["\'](/dashboard/[A-Za-z0-9_/\-]+)["\']', f.read_text(encoding="utf-8"))):
            if path not in pages:
                bad.append(f"{f.relative_to(root)} -> {path}")
    assert not bad, "links to pages that do not exist:\n" + "\n".join(bad)


async def test_bell_is_shipped_on_every_portal(app_client):
    js = (await app_client.get("/static/js/admin-common.js")).text
    for needle in ("wireNotifications", "bell-btn", "unread-count", "read-all", "Show older", "You\\u2019re all caught up", "quiet"):
        assert needle in js, needle
    css = (await app_client.get("/static/css/dashboard.css")).text
    assert ".bell-panel" in css and ".bell-count" in css
    for path in ("/dashboard/admin", "/dashboard/faculty", "/dashboard/student", "/dashboard/library", "/dashboard/accounts/fees"):
        assert "content-topbar-right" in (await app_client.get(path)).text, path