"""Head of Department / Coordinator: decides teachers' attendance corrections and watches teacher progress,
without the administrator's powers (locking attendance, marking for others, holidays)."""
from __future__ import annotations

import pytest

from app.auth.redirects import home_for_roles
from app.core.config import settings
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _create_n_sessions
from tests.integration.test_fees import _login, _login_admin
from tests.integration.test_result_sheets import _as_teacher, _class

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)


async def _hod(client, db):
    """A senior teacher given HOD as extra access."""
    await _login_admin(client)
    username, first = await _staff(client, db, "HODT", "TEACHER", "35202-4400001-1")
    emp = (await client.get("/api/v1/employees", params={"q": username})).json()["items"][0]["id"]
    r = await client.put(f"/api/v1/employees/{emp}/access-roles", json={"extra_roles": ["HOD"]})
    assert r.status_code == 200 and r.json()["roles"] == ["HOD", "TEACHER"]
    return username, await _first_login(client, username, first)


def test_hod_lands_on_the_department_portal():
    assert home_for_roles(["TEACHER", "HOD"]) == "/dashboard/hod"
    assert home_for_roles(["ADMIN", "HOD"]) == "/dashboard/admin"


async def test_hod_decides_a_teachers_correction_request(app_client, admin_user, db_session):
    offering, students, _a, teacher = await _class(app_client, db_session, code="HD1", n=2, cnic_base=4500)
    await _login_admin(app_client)
    session_id = (await _create_n_sessions(app_client, offering, 1))[0]
    await _as_teacher(app_client, teacher)
    req = await app_client.post(f"/api/v1/attendance/sessions/{session_id}/correction-request",
                                json={"reason": "Register was on paper that day", "present_student_ids": [students[0][0]]})
    assert req.status_code == 201, req.text
    # the teacher cannot decide their own request
    assert (await app_client.post(f"/api/v1/attendance/correction-requests/{req.json()['id']}/decision", json={"approve": True})).status_code == 403

    hod = await _hod(app_client, db_session)
    await _login(app_client, *hod)
    pending = (await app_client.get("/api/v1/attendance/correction-requests", params={"status": "PENDING"})).json()
    assert [p["id"] for p in pending] == [req.json()["id"]]                       # sees everyone's, not only their own
    ok = await app_client.post(f"/api/v1/attendance/correction-requests/{req.json()['id']}/decision", json={"approve": True, "note": "Checked the paper register"})
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
    assert (await app_client.get("/api/v1/attendance/needs-attention")).status_code == 200
    assert (await app_client.get("/api/v1/academics/teacher-progress")).status_code == 200


async def test_hod_has_no_administrator_powers(app_client, admin_user, db_session):
    offering, _s, _a, _t = await _class(app_client, db_session, code="HD2", n=1, cnic_base=4600, with_teacher=False)
    hod = await _hod(app_client, db_session)
    await _login(app_client, *hod)
    assert (await app_client.post(f"/api/v1/attendance/offerings/{offering}/lock")).status_code == 403
    assert (await app_client.get("/api/v1/policy")).status_code == 403
    assert (await app_client.get("/api/v1/employees")).status_code == 403
    page = await app_client.get("/dashboard/hod")
    assert page.status_code == 200 and 'data-allowed-roles="ADMIN,HOD"' in page.text
    assert 'data-tab="lock"' not in page.text and 'data-tab="corrections"' in page.text
    admin_page = await app_client.get("/dashboard/admin/attendance")
    assert 'data-tab="lock"' in admin_page.text and 'data-allowed-roles="ADMIN"' in admin_page.text