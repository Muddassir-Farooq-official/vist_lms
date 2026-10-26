"""Administrator "view as" (read-only) and the activity log."""
from __future__ import annotations

from app.auth.models import Role, User, UserRole
from app.core.security import hash_password
from tests.integration.test_announcements import _staff, _student
from tests.integration.test_fees import _login, _login_admin

V = "/api/v1/view-as"
A = "/api/v1/audit"


async def _principal(db_session):
    from sqlalchemy import select

    u = User(username="principal_va", password_hash=hash_password("Principal#Pass1"), must_change_password=False)
    db_session.add(u)
    await db_session.flush()
    role = (await db_session.execute(select(Role).where(Role.code == "PRINCIPAL"))).scalar_one()
    db_session.add(UserRole(user_id=u.id, role_id=role.id))
    await db_session.commit()
    return "principal_va", "Principal#Pass1"


async def test_admin_views_a_teacher_read_only_and_it_is_logged(app_client, admin_user, db_session):
    await _login_admin(app_client)
    username, _pw = await _staff(app_client, db_session, "VA_TEACH", "TEACHER", "35202-7700001-1")
    found = (await app_client.get(f"{V}/people", params={"q": username})).json()
    teacher = next(p for p in found if p["detail"] == username)
    assert teacher["can_view"] is True

    r = await app_client.post(f"{V}/start", json={"user_id": str(teacher["user_id"])})
    assert r.status_code == 200 and r.json()["redirect"] == "/dashboard/faculty"
    me = (await app_client.get("/api/v1/auth/me")).json()
    assert me["username"] == username and me["roles"] == ["TEACHER"] and me["viewing_as_by"]
    assert me["must_change_password"] is False                       # never sent to the teacher's own password form

    # Reading works; changing anything is refused, even things the teacher could do, and admin pages are closed.
    assert (await app_client.get("/api/v1/academics/offerings/me/overview")).status_code == 200
    blocked = await app_client.post("/api/v1/applications", json={})
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "READ_ONLY_VIEW"
    assert (await app_client.put("/api/v1/institute/profile", json={"name": "Hacked"})).status_code == 403
    assert (await app_client.get("/api/v1/reports/admin-dashboard")).status_code == 403
    assert (await app_client.post(f"{V}/start", json={"user_id": str(teacher["user_id"])})).status_code == 403

    out = await app_client.post(f"{V}/stop")
    assert out.status_code == 200 and out.json()["redirect"] == "/dashboard/admin/view-as"
    assert (await app_client.get("/api/v1/auth/me")).json()["username"] == "admin_test"

    log = (await app_client.get(f"{A}/activity", params={"action": "view_as"})).json()
    actions = [x["action"] for x in log["items"]]
    assert "view_as.started" in actions and "view_as.ended" in actions
    assert all(x["actor"]["username"] == "admin_test" and x["subject"] == username for x in log["items"])


async def test_administrators_cannot_be_viewed_and_others_cannot_view(app_client, admin_user, db_session):
    await _login_admin(app_client)
    assert (await app_client.post(f"{V}/start", json={"user_id": str(admin_user.id)})).status_code == 403
    sid = await _student(app_client, 71)
    assert sid
    user, pw = await _principal(db_session)
    await _login(app_client, user, pw)
    assert (await app_client.get(f"{V}/people", params={"q": "Stu"})).status_code == 403
    assert (await app_client.post(f"{V}/start", json={"user_id": str(admin_user.id)})).status_code == 403


async def test_activity_log_filters_details_and_export(app_client, admin_user, db_session):
    await _login_admin(app_client)
    await _student(app_client, 72)
    page = (await app_client.get(f"{A}/activity")).json()
    assert page["total"] >= 2 and page["can_export"] is True
    first = page["items"][0]
    assert {"at", "what", "actor", "entity_type", "ip"} <= set(first)
    assert first["actor"]["kind"] in ("admin", "staff", "student", "parent", "system")
    admins = (await app_client.get(f"{A}/activity", params={"kind": "admin"})).json()
    assert admins["total"] >= 1 and all(x["actor"]["kind"] == "admin" for x in admins["items"])
    assert (await app_client.get(f"{A}/activity", params={"kind": "nobody"})).status_code == 422
    one = (await app_client.get(f"{A}/activity/{first['id']}")).json()
    assert "before" in one and "after" in one and "metadata" in one
    facets = (await app_client.get(f"{A}/activity-facets")).json()
    assert "auth" in facets["action_groups"]
    csv = await app_client.get(f"{A}/activity.csv")
    assert csv.status_code == 200 and csv.text.lstrip("﻿").startswith("When (UTC),Who,Username,Kind")
    exported = (await app_client.get(f"{A}/activity", params={"action": "audit.exported"})).json()
    assert exported["total"] == 1                                      # taking a copy is itself recorded


async def test_principal_reads_the_activity_log_but_cannot_export(app_client, admin_user, db_session):
    user, pw = await _principal(db_session)
    await _login(app_client, user, pw)
    page = await app_client.get(f"{A}/activity")
    assert page.status_code == 200 and page.json()["can_export"] is False
    assert (await app_client.get(f"{A}/activity.csv")).status_code == 403
    assert (await app_client.get("/dashboard/principal/activity")).status_code == 200


async def test_pages_and_menus(app_client, admin_user):
    from app.main import ADMIN_NAV_ITEMS, PRINCIPAL_NAV_ITEMS

    hrefs = {i["href"] for i in ADMIN_NAV_ITEMS}
    assert {"/dashboard/admin/view-as", "/dashboard/admin/activity", "/dashboard/accounts", "/dashboard/exam/home",
            "/dashboard/gate/home", "/dashboard/warden/home", "/dashboard/principal"} <= hrefs
    assert "/dashboard/principal/activity" in {i["href"] for i in PRINCIPAL_NAV_ITEMS}
    await _login_admin(app_client)
    for path in ("/dashboard/admin/view-as", "/dashboard/admin/activity"):
        assert (await app_client.get(path)).status_code == 200
    assert 'href="/dashboard/admin/activity"' in (await app_client.get("/dashboard/admin")).text
