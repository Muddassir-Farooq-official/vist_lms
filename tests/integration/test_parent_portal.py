"""Parent portal: the administration creates a guardian login from a student; the guardian sees only their own
children (brothers and sisters with the same guardian CNIC are linked together), read-only."""
from __future__ import annotations

import pytest

from app.auth.redirects import home_for_roles
from app.core.config import settings
from tests.integration.test_fees import (
    _create_active_fee_structure,
    _create_and_activate_student,
    _login,
    _login_admin,
    _student_payload,
)

pytestmark = pytest.mark.asyncio
P = "/api/v1/parents"
GUARDIAN = "55555-4444444-4"            # the guardian CNIC in the shared student test payload


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _parent_login(client, username, temp):
    await _login(client, username, temp)
    new = "Parent#Pass-2026"
    assert (await client.post("/api/v1/auth/change-password", json={"current_password": temp, "new_password": new})).status_code == 204
    await _login(client, username, new)


def test_parents_land_on_the_parent_portal():
    assert home_for_roles(["PARENT"]) == "/dashboard/parent"


async def test_guardian_follows_both_children_and_nobody_else(app_client, admin_user):
    a, _ua = await _create_and_activate_student(app_client, cnic="76001-0000001-1")
    b, _ub = await _create_and_activate_student(app_client, cnic="76001-0000002-2")          # same guardian -> sibling
    await _login_admin(app_client)
    other = await app_client.post("/api/v1/students", json=_student_payload(cnic="76001-0000003-3", guardian_cnic="76009-9999999-9"))
    assert other.status_code == 201, other.text
    other_id = other.json()["id"]
    await _create_active_fee_structure(app_client, total_amount=6000, installment_count=1)
    await _login_admin(app_client)

    before = (await app_client.get(f"{P}/students/{a}")).json()
    assert before["has_login"] is False and before["username"] == GUARDIAN.replace("-", "")
    made = (await app_client.post(f"{P}/students/{a}")).json()
    assert made["temporary_password"] and made["children_linked"] == 2
    again = (await app_client.post(f"{P}/students/{b}")).json()
    assert again["temporary_password"] is None                                                  # existing login, nothing new to hand out

    await _parent_login(app_client, made["username"], made["temporary_password"])
    kids = (await app_client.get(f"{P}/me/children")).json()
    assert {k["id"] for k in kids} == {a, b}
    view = (await app_client.get(f"{P}/me/children/{a}")).json()
    assert set(view) >= {"attendance", "results", "fees"} and view["fees"]["summary"]["remaining"] == 6000.0
    challan = view["fees"]["assignments"][0]["installments"][0]["challan"]["id"]
    assert (await app_client.get(f"/api/v1/fees/challans/{challan}/pdf")).status_code == 200
    assert (await app_client.get(f"{P}/me/children/{other_id}")).status_code == 404               # not their child
    # read-only: no staff or student screens
    assert (await app_client.get("/api/v1/students")).status_code == 403
    assert (await app_client.get("/api/v1/fees/accounts/me")).status_code in (403, 404)
    page = await app_client.get("/dashboard/parent")
    assert page.status_code == 200 and 'data-allowed-roles="PARENT"' in page.text


async def test_other_childs_challan_is_hidden_and_revoking_removes_access(app_client, admin_user):
    a, _ua = await _create_and_activate_student(app_client, cnic="76002-0000001-1")
    await _login_admin(app_client)
    other = await app_client.post("/api/v1/students", json=_student_payload(cnic="76002-0000003-3", guardian_cnic="76008-8888888-8"))
    other_id = other.json()["id"]
    await _create_active_fee_structure(app_client, total_amount=6000, installment_count=1)
    await _login_admin(app_client)
    made = (await app_client.post(f"{P}/students/{a}")).json()
    other_challan = (await app_client.get(f"/api/v1/fees/accounts/students/{other_id}")).json()["assignments"][0]["installments"][0]["challan"]["id"]
    await _parent_login(app_client, made["username"], made["temporary_password"])
    assert (await app_client.get(f"/api/v1/fees/challans/{other_challan}/pdf")).status_code == 404
    await _login_admin(app_client)
    assert (await app_client.delete(f"{P}/students/{a}")).status_code == 204
    await _login(app_client, made["username"], "Parent#Pass-2026")
    assert (await app_client.get(f"{P}/me/children")).json() == []


async def test_only_the_administration_manages_parent_access(app_client, admin_user):
    a, ua = await _create_and_activate_student(app_client, cnic="76003-0000001-1")
    await _login(app_client, ua, "StudentPass123!")
    assert (await app_client.post(f"{P}/students/{a}")).status_code == 403
    assert (await app_client.get(f"{P}/me/children")).status_code == 403