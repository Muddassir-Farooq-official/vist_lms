"""Principal / Director: a read-only institute overview, with numbers that add up, and no power to change anything."""
from __future__ import annotations

import pytest

from app.auth.redirects import home_for_roles
from app.core.config import settings
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_fees import _create_active_fee_structure, _create_and_activate_student, _login, _login_admin

pytestmark = pytest.mark.asyncio
OV = "/api/v1/reports/institute-overview"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)


async def _principal(client, db):
    await _login_admin(client)
    username, first = await _staff(client, db, "PRIN", "TEACHER", "35202-4800001-1")
    emp = (await client.get("/api/v1/employees", params={"q": username})).json()["items"][0]["id"]
    assert (await client.put(f"/api/v1/employees/{emp}/access-roles", json={"extra_roles": ["PRINCIPAL"]})).status_code == 200
    return username, await _first_login(client, username, first)


def test_principal_lands_on_the_overview():
    assert home_for_roles(["TEACHER", "PRINCIPAL"]) == "/dashboard/principal"
    assert home_for_roles(["ADMIN", "PRINCIPAL"]) == "/dashboard/admin"


async def test_overview_numbers_add_up(app_client, admin_user):
    sid, _u = await _create_and_activate_student(app_client, cnic="75001-0000001-1")
    await _create_active_fee_structure(app_client, total_amount=10000, installment_count=2)
    await _login_admin(app_client)
    aid = next(a for a in (await app_client.get("/api/v1/fees/assignments")).json() if a["payer_id"] == sid)["id"]
    assert (await app_client.post(f"/api/v1/fees/assignments/{aid}/payments", json={"amount": 2500, "method": "CASH"})).status_code == 201
    d = (await app_client.get(OV)).json()
    assert d["students"]["by_status"]["ACTIVE"] >= 1
    assert d["fees"]["charged"] == 10000 and d["fees"]["outstanding"] == 7500 and d["fees"]["collected_this_month"] == 2500
    assert d["fees"]["collection_rate_percent"] == 25.0
    assert set(d) == {"open_terms", "students", "teaching", "attendance", "results", "fees"}


async def test_principal_sees_everything_and_changes_nothing(app_client, admin_user, db_session):
    p = await _principal(app_client, db_session)
    await _login(app_client, *p)
    assert (await app_client.get(OV)).status_code == 200
    assert (await app_client.get("/api/v1/academics/teacher-progress")).status_code == 200
    page = await app_client.get("/dashboard/principal")
    assert page.status_code == 200 and "principal-overview.js" in page.text
    # no changes
    assert (await app_client.post("/api/v1/academics/courses", json={"code": "PR1", "title": "x", "credit_hours": 3})).status_code == 403
    assert (await app_client.put("/api/v1/policy", json={"values": {"attendance_fine_amount": 1}})).status_code == 403
    assert (await app_client.get("/api/v1/employees")).status_code == 403
    assert (await app_client.get("/api/v1/attendance/correction-requests/x/decision")).status_code in (403, 404, 405, 422)


async def test_teachers_and_students_cannot_see_the_overview(app_client, admin_user):
    _sid, user = await _create_and_activate_student(app_client, cnic="75001-0000002-2")
    await _login(app_client, user, "StudentPass123!")
    assert (await app_client.get(OV)).status_code == 403