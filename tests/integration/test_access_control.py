"""Access control (1.71.0): custom roles, a built-in role's rights changed (and kept through deploys), one person's own
rights with end dates, the safety rules (Administrator locked, no self-change, locked rights, staff only), a second
administrator for money/control rights, history with undo, and the pages extra rights open."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.access.models import RolePermissionRemoval
from app.auth.models import Permission, Role, RolePermission, User
from app.core.clock import institute_today
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_fees import _login, _login_admin
from tests.integration.test_smart_timetable import _q as _q  # noqa: F401  (rate limits off)

pytestmark = pytest.mark.asyncio
A = "/api/v1/access"
TT = "/api/v1/academics/timetable/smart/latest"            # needs timetable.manage
MARK = "/api/v1/attendance/sessions/{}/mark"                # needs attendance.mark


async def _person(client, db, code, role, cnic):
    await _login_admin(client)
    username, first = await _staff(client, db, code, role, cnic)
    pw = await _first_login(client, username, first)
    await _login_admin(client)
    items = (await client.get("/api/v1/employees", params={"q": username})).json()["items"]
    user = (await db.execute(select(User).where(User.username == username))).scalar_one()
    return {"username": username, "pw": pw, "emp": items[0]["id"], "user_id": str(user.id)}


async def _role_id(client, code):
    return next(r["id"] for r in (await client.get(A)).json()["roles"] if r["code"] == code)


async def test_a_custom_role_gives_a_teacher_the_timetable_and_only_its_pages(app_client, admin_user, db_session):
    t = await _person(app_client, db_session, "TCA", "TEACHER", "35202-8100001-1")
    await _login(app_client, t["username"], t["pw"])
    assert (await app_client.get(TT)).status_code == 403
    me = (await app_client.get("/api/v1/auth/me")).json()
    assert me["extra_permissions"] == [] and me["admin_home"] is None

    await _login_admin(app_client)
    r = await app_client.post(f"{A}/roles", json={"name": "Timetable assistant", "description": "Keeps the timetable",
                                                  "rights": ["timetable.manage", "course.view", "program.view"]})
    assert r.status_code == 201, r.text
    code = r.json()["code"]
    assert code.startswith("C_TIMETABLE")
    roles = (await app_client.get(f"/api/v1/employees/{t['emp']}/access-roles")).json()
    assert code in roles["grantable"] and roles["names"][code] == "Timetable assistant"
    prev = (await app_client.post(f"{A}/people/{t['user_id']}/preview", json={"extra_roles": [code]})).json()
    assert [g["code"] for g in prev["gained"]] == ["timetable.manage"] and prev["pages_gained"] == ["timetable"]
    assert (await app_client.put(f"{A}/employees/{t['emp']}/roles", json={"extra_roles": [code]})).json()["done"] is True

    await _login(app_client, t["username"], t["pw"])
    assert (await app_client.get(TT)).status_code == 200
    me = (await app_client.get("/api/v1/auth/me")).json()
    assert me["extra_permissions"] == ["timetable.manage"] and me["admin_home"] == "/dashboard/admin/timetable"
    assert (await app_client.get(f"{A}/my-pages")).json() == {"all": False, "pages": ["timetable"]}
    assert (await app_client.get(A)).status_code == 403                                      # access control stays with administrators
    page = await app_client.get("/dashboard/admin/timetable")
    assert 'data-page-rights="timetable.manage"' in page.text

    # a role somebody has cannot be deleted; once nobody has it, it can
    await _login_admin(app_client)
    rid = await _role_id(app_client, code)
    assert (await app_client.delete(f"{A}/roles/{rid}")).status_code == 422
    await app_client.put(f"{A}/employees/{t['emp']}/roles", json={"extra_roles": []})
    assert (await app_client.delete(f"{A}/roles/{rid}")).status_code == 200


async def test_a_right_taken_from_a_built_in_role_stays_taken_after_a_deploy(app_client, admin_user, db_session):
    t = await _person(app_client, db_session, "TCB", "TEACHER", "35202-8100002-2")
    teacher = await _role_id(app_client, "TEACHER")
    rights = (await app_client.get(A)).json()["roles"]
    now = next(r for r in rights if r["code"] == "TEACHER")["rights"]
    assert "attendance.mark" in now and "assignment.manage" in now
    keep = [c for c in now if c != "attendance.mark"]
    r = await app_client.put(f"{A}/roles/{teacher}/rights", json={"rights": keep})
    assert r.status_code == 200 and r.json()["done"] is True, r.text
    after = next(x for x in (await app_client.get(A)).json()["roles"] if x["code"] == "TEACHER")
    assert after["removed"] == ["attendance.mark"] and "assignment.manage" in after["rights"]        # own-use rights are kept
    await _login(app_client, t["username"], t["pw"])
    assert (await app_client.post(MARK.format(uuid.uuid4()), json={})).status_code == 403

    # the deploy's seed adds the standard rights again, except the one an administrator took away
    from scripts.seed_permissions import seed

    await db_session.commit()
    await seed(False, None, None)
    perm = (await db_session.execute(select(Permission).where(Permission.code == "attendance.mark"))).scalar_one()
    role = (await db_session.execute(select(Role).where(Role.code == "TEACHER"))).scalar_one()
    assert await db_session.get(RolePermission, (role.id, perm.id)) is None
    assert await db_session.get(RolePermissionRemoval, (role.id, perm.id)) is not None

    # undo puts it back (and the deploy may then seed it as usual)
    await _login_admin(app_client)
    change = next(h for h in (await app_client.get(f"{A}/history")).json() if h["kind"] == "ROLE_RIGHTS")
    assert (await app_client.post(f"{A}/history/{change['id']}/undo")).json()["done"] is True
    teacher_now = next(x for x in (await app_client.get(A)).json()["roles"] if x["code"] == "TEACHER")
    assert "attendance.mark" in teacher_now["rights"] and teacher_now["removed"] == []
    hist = (await app_client.get(f"{A}/history")).json()
    assert any(h["undone_at"] for h in hist) and any(h["undo_of"] for h in hist)


async def test_one_persons_own_rights_with_an_end_date_and_a_taken_away_right(app_client, admin_user, db_session):
    t = await _person(app_client, db_session, "TCC", "TEACHER", "35202-8100003-3")
    tomorrow = (institute_today() + timedelta(days=1)).isoformat()
    body = {"overrides": [{"code": "timetable.manage", "effect": "GRANT", "until": tomorrow, "reason": "exam week"},
                          {"code": "attendance.mark", "effect": "DENY"}]}
    prev = (await app_client.post(f"{A}/people/{t['user_id']}/preview", json=body)).json()
    assert [x["code"] for x in prev["gained"]] == ["timetable.manage"] and [x["code"] for x in prev["lost"]] == ["attendance.mark"]
    assert (await app_client.put(f"{A}/people/{t['user_id']}/rights", json=body)).json()["done"] is True
    await _login(app_client, t["username"], t["pw"])
    assert (await app_client.get(TT)).status_code == 200
    assert (await app_client.post(MARK.format(uuid.uuid4()), json={})).status_code == 403

    # the end date passes: the right is gone by itself
    from app.access.models import UserAccessOverride

    ov = (await db_session.execute(select(UserAccessOverride).where(UserAccessOverride.permission_code == "timetable.manage"))).scalar_one()
    ov.until = institute_today() - timedelta(days=1)
    await db_session.commit()
    from app.core import dependencies

    dependencies.forget_all_sessions()
    assert (await app_client.get(TT)).status_code == 403
    await _login_admin(app_client)
    person = (await app_client.get(f"{A}/people/{t['user_id']}")).json()
    assert next(o for o in person["overrides"] if o["code"] == "timetable.manage")["expired"] is True

    # refused: a date in the past, a right that stays with the Administrator role, an own-use right
    bad = await app_client.put(f"{A}/people/{t['user_id']}/rights", json={"overrides": [{"code": "timetable.manage", "effect": "GRANT", "until": "2020-01-01"}]})
    assert bad.status_code == 422
    assert (await app_client.put(f"{A}/people/{t['user_id']}/rights", json={"overrides": [{"code": "role.manage", "effect": "GRANT"}]})).status_code == 422
    assert (await app_client.put(f"{A}/people/{t['user_id']}/rights", json={"overrides": [{"code": "enrollment.self", "effect": "GRANT"}]})).status_code == 422


async def test_safety_rules_administrator_locked_no_self_change_staff_only(app_client, admin_user, db_session):
    await _login_admin(app_client)
    admin_role = await _role_id(app_client, "ADMIN")
    r = await app_client.put(f"{A}/roles/{admin_role}/rights", json={"rights": []})
    assert r.status_code == 422 and "Administrator" in r.text
    assert (await app_client.put(f"{A}/people/{admin_user.id}/rights", json={"overrides": [{"code": "timetable.manage", "effect": "DENY"}]})).status_code == 422
    # a right that stays with the Administrator role cannot be put on another role
    teacher = await _role_id(app_client, "TEACHER")
    assert (await app_client.put(f"{A}/roles/{teacher}/rights", json={"rights": ["role.manage"]})).status_code == 422
    # students do not get rights here
    s = await app_client.post("/api/v1/students", json={"first_name": "Acc", "last_name": "Ess", "date_of_birth": "2005-01-01", "gender": "Male",
                                                         "cnic": "35202-8100009-9", "contact_no": "03001234567", "marital_status": "Single",
                                                         "religion": "Islam", "nationality": "Pakistani", "guardian_name": "G", "guardian_cnic": "35202-8100008-8",
                                                         "relation_with_guardian": "Father", "emergency_contact_no": "03001234568", "present_address": "X",
                                                         "province": "Punjab", "district": "Lahore", "city": "Lahore"})
    suser = (await db_session.execute(select(User).where(User.username == s.json()["username"]))).scalar_one()
    r = await app_client.put(f"{A}/people/{suser.id}/rights", json={"overrides": [{"code": "timetable.manage", "effect": "GRANT"}]})
    assert r.status_code == 422 and "staff" in r.text
    # nobody changes their own access
    t = await _person(app_client, db_session, "TCD", "TEACHER", "35202-8100004-4")
    await app_client.put(f"/api/v1/employees/{t['emp']}/access-roles", json={"extra_roles": ["ADMIN"]})
    await _login(app_client, t["username"], t["pw"])
    own = await app_client.put(f"{A}/employees/{t['emp']}/roles", json={"extra_roles": []})
    assert own.status_code == 422 and "your own" in own.text


async def test_money_rights_wait_for_a_second_administrator(app_client, admin_user, db_session):
    second = await _person(app_client, db_session, "TCE", "TEACHER", "35202-8100005-5")
    await app_client.put(f"/api/v1/employees/{second['emp']}/access-roles", json={"extra_roles": ["ADMIN"]})   # alone: goes ahead
    acc = await _person(app_client, db_session, "TCF", "ACCOUNTS_OFFICER", "35202-8100006-6")

    r = (await app_client.put(f"{A}/people/{acc['user_id']}/rights", json={"overrides": [{"code": "payment.reverse", "effect": "GRANT"}]})).json()
    assert r["waiting"] is True and r["done"] is False
    person = (await app_client.get(f"{A}/people/{acc['user_id']}")).json()
    assert "payment.reverse" not in person["rights"]                                       # nothing changed yet
    waiting = (await app_client.get(f"{A}/waiting")).json()
    assert len(waiting) == 1 and "Reverse payments" in waiting[0]["reason"]
    assert (await app_client.get("/api/v1/approvals")).json() == []                         # not on the general Approvals page
    own = await app_client.post(f"{A}/waiting/{waiting[0]['id']}/decide", json={"approve": True})
    assert own.status_code == 422                                                          # not the one who asked

    await _login(app_client, second["username"], second["pw"])
    # (the second administrator signs in with a code in real life; tests run without it)
    ok = await app_client.post(f"{A}/waiting/{waiting[0]['id']}/decide", json={"approve": True})
    assert ok.status_code == 200, ok.text
    person = (await app_client.get(f"{A}/people/{acc['user_id']}")).json()
    assert "payment.reverse" in person["rights"]
    hist = (await app_client.get(f"{A}/history")).json()
    assert hist[0]["kind"] == "PERSON_RIGHTS" and hist[0]["confirmed_by_second_admin"] is True

    # a non-money right goes ahead at once
    r = (await app_client.put(f"{A}/people/{acc['user_id']}/rights", json={"overrides": [
        {"code": "payment.reverse", "effect": "GRANT"}, {"code": "timetable.manage", "effect": "GRANT"}]})).json()
    assert r["done"] is True


async def test_whoever_holds_a_right_through_their_own_grant_counts_as_an_approver(app_client, admin_user, db_session):
    from app.access.resolve import users_with_permission

    t = await _person(app_client, db_session, "TCG", "TEACHER", "35202-8100007-7")
    assert uuid.UUID(t["user_id"]) not in await users_with_permission(db_session, "attendance.correction.decide")
    await app_client.put(f"{A}/people/{t['user_id']}/rights", json={"overrides": [{"code": "attendance.correction.decide", "effect": "GRANT"}]})
    assert uuid.UUID(t["user_id"]) in await users_with_permission(db_session, "attendance.correction.decide")
    await app_client.put(f"{A}/people/{t['user_id']}/rights", json={"overrides": [{"code": "attendance.mark", "effect": "DENY"}]})
    assert uuid.UUID(t["user_id"]) not in await users_with_permission(db_session, "attendance.mark")


async def test_the_page_and_catalogue(app_client, admin_user):
    await _login_admin(app_client)
    page = await app_client.get("/dashboard/admin/access")
    assert page.status_code == 200 and "access-control.js" in page.text
    data = (await app_client.get(A)).json()
    keys = [m["key"] for m in data["modules"]]
    assert {"timetable", "attendance", "exams", "fees", "payroll"} <= set(keys)
    assert next(r for r in data["roles"] if r["code"] == "ADMIN")["locked"] is True
    assert data["page_rights"]["timetable"] == ["timetable.manage"]


async def test_students_parents_and_alumni_roles_never_get_office_rights(app_client, admin_user):
    await _login_admin(app_client)
    for code in ("STUDENT", "PARENT", "ALUMNUS"):
        rid = await _role_id(app_client, code)
        r = await app_client.put(f"{A}/roles/{rid}/rights", json={"rights": ["payment.record"]})
        assert r.status_code == 422 and "staff only" in r.text


async def test_money_rights_given_here_need_the_sign_in_code_and_the_person_is_told(app_client, admin_user, db_session, monkeypatch):
    from app.auth import two_factor as tf
    from app.core.config import settings
    from app.notifications.models import Notification

    t = await _person(app_client, db_session, "TCH", "TEACHER", "35202-8100011-1")
    assert tf.required_for(["TEACHER"], []) is False
    r = await app_client.put(f"{A}/people/{t['user_id']}/rights", json={"overrides": [{"code": "payment.reverse", "effect": "GRANT"}]})
    assert r.json()["done"] is True                                               # the only administrator: goes ahead
    note = (await db_session.execute(select(Notification).where(Notification.user_id == uuid.UUID(t["user_id"]),
                                                                Notification.title == "Your access changed"))).scalars().all()
    assert note and "Reverse payments" in note[-1].message
    monkeypatch.setattr(settings, "two_factor_required_for_admins", 1)
    login = await app_client.post("/api/v1/auth/login", json={"username": t["username"], "password": t["pw"]})
    assert login.json()["two_factor"] == "setup"                                   # money right given here -> code required
    # a non-money right does not ask for the code
    monkeypatch.setattr(settings, "two_factor_required_for_admins", 0)
    await _login_admin(app_client)
    await app_client.put(f"{A}/people/{t['user_id']}/rights", json={"overrides": [{"code": "timetable.manage", "effect": "GRANT"}]})
    monkeypatch.setattr(settings, "two_factor_required_for_admins", 1)
    login = await app_client.post("/api/v1/auth/login", json={"username": t["username"], "password": t["pw"]})
    assert not login.json().get("two_factor")


async def test_end_date_reminder_once_and_who_can_and_giving_a_role_to_many(app_client, admin_user, db_session):
    from app.access.service import end_date_round
    from app.notifications.models import Notification

    a = await _person(app_client, db_session, "TCI", "TEACHER", "35202-8100012-2")
    b = await _person(app_client, db_session, "TCJ", "TEACHER", "35202-8100013-3")
    soon = (institute_today() + timedelta(days=2)).isoformat()
    await app_client.put(f"{A}/people/{a['user_id']}/rights", json={"overrides": [{"code": "timetable.manage", "effect": "GRANT", "until": soon}]})
    assert await end_date_round(db_session) == 1
    assert await end_date_round(db_session) == 0                                  # once only
    told = (await db_session.execute(select(Notification).where(Notification.title == "Access ending soon"))).scalars().all()
    assert {n.user_id for n in told} >= {uuid.UUID(a["user_id"]), admin_user.id}

    who = (await app_client.get(f"{A}/who", params={"code": "timetable.manage"})).json()
    mine = next(p for p in who["people"] if p["user_id"] == a["user_id"])
    assert mine["has_it"] and mine["own"]["until"] == soon and mine["roles"] == []
    assert any(p["user_id"] == str(admin_user.id) and p["has_it"] and p["roles"] for p in who["people"])

    r = await app_client.post(f"{A}/roles", json={"name": "Duty roster"})
    rid = r.json()["id"]
    res = (await app_client.post(f"{A}/roles/{rid}/members", json={"add": [a["emp"], b["emp"]], "remove": []})).json()
    assert res["done"] == 2 and res["failed"] == 0
    members = (await app_client.get(f"{A}/roles/{rid}/members")).json()
    assert {m["employee_id"] for m in members} == {a["emp"], b["emp"]}
    res = (await app_client.post(f"{A}/roles/{rid}/members", json={"add": [], "remove": [b["emp"]]})).json()
    assert res["done"] == 1 and len((await app_client.get(f"{A}/roles/{rid}/members")).json()) == 1
    # a role that comes with a job title is not taken away here
    teacher = await _role_id(app_client, "TEACHER")
    res = (await app_client.post(f"{A}/roles/{teacher}/members", json={"add": [], "remove": [a["emp"]]})).json()
    assert res["failed"] == 1 and "job title" in res["results"][0]["message"]
