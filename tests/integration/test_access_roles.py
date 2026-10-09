"""Extra access roles: a staff member can hold more than the job title's role (e.g. Exam Controller + Teacher,
a second Administrator), with guards on Administrator access."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.auth.models import User
from app.core.exceptions import BusinessRuleViolationError
from app.employees.access_roles import AccessRoleService
from tests.integration.test_exam_controller import _exam_user
from tests.integration.test_fees import _login, _login_admin

pytestmark = pytest.mark.asyncio


async def _emp_id(client, username):
    await _login_admin(client)
    items = (await client.get("/api/v1/employees", params={"q": username})).json()["items"]
    return items[0]["id"]


async def test_exam_controller_can_also_be_a_teacher(app_client, admin_user, db_session):
    user, pw = await _exam_user(app_client, db_session)
    emp = await _emp_id(app_client, user)
    before = (await app_client.get(f"/api/v1/employees/{emp}/access-roles")).json()
    assert before["main_role"] == "EXAM_CONTROLLER" and before["extra_roles"] == [] and "EXAM_CONTROLLER" not in before["grantable"]
    r = await app_client.put(f"/api/v1/employees/{emp}/access-roles", json={"extra_roles": ["TEACHER"]})
    assert r.status_code == 200 and r.json()["roles"] == ["EXAM_CONTROLLER", "TEACHER"]
    await _login(app_client, user, pw)
    me = (await app_client.get("/api/v1/auth/me")).json()
    assert set(me["roles"]) == {"EXAM_CONTROLLER", "TEACHER"}
    assert (await app_client.get("/api/v1/results/sheets")).status_code == 200                    # exam controller work
    assert (await app_client.get("/api/v1/academics/offerings/me")).status_code == 200          # teacher work


async def test_second_administrator_and_their_own_admin_cannot_be_self_removed(app_client, admin_user, db_session):
    user, pw = await _exam_user(app_client, db_session)
    emp = await _emp_id(app_client, user)
    assert (await app_client.put(f"/api/v1/employees/{emp}/access-roles", json={"extra_roles": ["ADMIN"]})).status_code == 200
    await _login(app_client, user, pw)
    assert (await app_client.get("/api/v1/policy")).status_code == 200                              # administrator-only screen
    own = await app_client.put(f"/api/v1/employees/{emp}/access-roles", json={"extra_roles": []})
    assert own.status_code == 422 and "your own Administrator" in own.text
    # the first administrator can take it away again
    await _login_admin(app_client)
    assert (await app_client.put(f"/api/v1/employees/{emp}/access-roles", json={"extra_roles": []})).json()["roles"] == ["EXAM_CONTROLLER"]


async def test_last_active_administrator_is_never_removed(app_client, admin_user, db_session):
    user, _pw = await _exam_user(app_client, db_session)
    emp = await _emp_id(app_client, user)
    await app_client.put(f"/api/v1/employees/{emp}/access-roles", json={"extra_roles": ["ADMIN"]})
    admin_user.is_active = False                     # now this person is the only active administrator
    await db_session.flush()
    with pytest.raises(BusinessRuleViolationError, match="last active administrator"):
        await AccessRoleService(db_session).set_extra(uuid.UUID(emp), [], actor_user_id=uuid.uuid4())


async def test_change_signs_the_person_out_and_bad_requests_are_refused(app_client, admin_user, db_session):
    user, pw = await _exam_user(app_client, db_session)
    emp = await _emp_id(app_client, user)
    student = await app_client.put(f"/api/v1/employees/{emp}/access-roles", json={"extra_roles": ["STUDENT"]})
    assert student.status_code == 422
    # signed in as the exam controller, then an admin changes their access -> their session is ended
    await _login(app_client, user, pw)
    cookies = dict(app_client.cookies)
    assert (await app_client.put(f"/api/v1/employees/{emp}/access-roles", json={"extra_roles": ["LIBRARIAN"]})).status_code == 403   # not theirs to manage
    await _login_admin(app_client)
    await app_client.put(f"/api/v1/employees/{emp}/access-roles", json={"extra_roles": ["LIBRARIAN"]})
    app_client.cookies.clear()
    for k, v in cookies.items():
        app_client.cookies.set(k, v)
    refreshed = await app_client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 401