"""Regression tests for defects found in the pre-launch audit:

1. Person search (students, employees) could never match a full
   "First Last" query, and treated % / _ as wildcards.
2. Creating an employee accepted a deactivated job role, and silently
   produced a permission-less account when the mapped system role was
   missing.
3. Changing or admin-resetting a password left every other login
   session (refresh token) alive.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import delete

from app.auth.models import Role
from app.employees.models import JobRole
from app.students.models import Student, StudentStatus

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


async def _login(app_client, username, password):
    resp = await app_client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return app_client.cookies.get("refresh_token")


async def _login_admin(app_client):
    return await _login(app_client, ADMIN["username"], ADMIN["password"])


async def _make_job_role(db_session, *, code="TCH", default_role_code="TEACHER", is_active=True) -> JobRole:
    role = JobRole(code=code, title="Teacher", default_role_code=default_role_code, is_active=is_active)
    db_session.add(role)
    await db_session.commit()
    return role


def _employee_payload(job_role_id, **overrides):
    payload = {
        "first_name": "Ali", "last_name": "Raza", "date_of_birth": "1990-05-01", "gender": "Male",
        "cnic": "11122-3334445-5", "contact_no": "03001112233", "hire_date": "2026-08-01",
        "department": "Computer Science", "job_role_id": str(job_role_id), "base_salary": 50000,
        "password": "TeacherPass123!",
    }
    payload.update(overrides)
    return payload


class TestPersonSearch:
    async def test_employee_search_matches_full_name_in_either_order_and_partials(
        self, app_client, admin_user, db_session
    ):
        job_role = await _make_job_role(db_session)
        await _login_admin(app_client)
        created = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id))
        assert created.status_code == 201, created.text
        employee_id = created.json()["id"]

        for query in ("Ali Raza", "Raza Ali", "ali", "raz", "ali r"):
            resp = await app_client.get("/api/v1/employees", params={"q": query})
            assert resp.status_code == 200, resp.text
            assert employee_id in [e["id"] for e in resp.json()["items"]], f"query {query!r} missed the employee"

    async def test_search_does_not_match_when_a_word_is_absent(self, app_client, admin_user, db_session):
        job_role = await _make_job_role(db_session)
        await _login_admin(app_client)
        await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id))

        resp = await app_client.get("/api/v1/employees", params={"q": "Ali Nobody"})
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    async def test_percent_sign_is_searched_literally_not_as_a_wildcard(self, app_client, admin_user, db_session):
        job_role = await _make_job_role(db_session)
        await _login_admin(app_client)
        await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id))

        resp = await app_client.get("/api/v1/employees", params={"q": "%"})
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    async def test_student_search_matches_full_name(self, app_client, admin_user, db_session):
        student = Student(
            first_name="Nida", last_name="Fatima", date_of_birth=date(2004, 1, 1), gender="Female",
            cnic="44444-5555566-7", contact_no="03004445556", marital_status="Single", religion="Islam",
            nationality="Pakistani", status=StudentStatus.ACTIVE, roll_number="SRCH-01",
        )
        db_session.add(student)
        await db_session.commit()
        await _login_admin(app_client)

        for query in ("Nida Fatima", "Fatima Nida", "nid"):
            resp = await app_client.get("/api/v1/students", params={"q": query})
            assert resp.status_code == 200, resp.text
            assert str(student.id) in [s["id"] for s in resp.json()["items"]], f"query {query!r} missed the student"


class TestEmployeeCreationGuards:
    async def test_cannot_hire_into_a_deactivated_job_role(self, app_client, admin_user, db_session):
        job_role = await _make_job_role(db_session, is_active=False)
        await _login_admin(app_client)

        resp = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id))
        assert resp.status_code == 422
        assert "deactivated" in resp.json()["error"]["message"].lower()

    async def test_missing_system_role_is_a_clear_error_not_a_permissionless_account(
        self, app_client, admin_user, db_session
    ):
        job_role = await _make_job_role(db_session, code="WARD", default_role_code="HOSTEL_WARDEN")
        await db_session.execute(delete(Role).where(Role.code == "HOSTEL_WARDEN"))
        await db_session.commit()
        await _login_admin(app_client)

        resp = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id))
        assert resp.status_code == 422
        assert "seed" in resp.json()["error"]["message"].lower()

        # Nothing half-created: the employee list is still empty.
        listing = await app_client.get("/api/v1/employees")
        assert listing.json()["items"] == []


class TestSessionRevocation:
    async def test_changing_password_ends_other_sessions_but_keeps_the_current_one(
        self, app_client, admin_user
    ):
        other_session_token = await _login_admin(app_client)   # e.g. a stolen/forgotten device
        current_session_token = await _login_admin(app_client)  # this browser
        assert other_session_token != current_session_token

        change = await app_client.post(
            "/api/v1/auth/change-password",
            json={"current_password": ADMIN["password"], "new_password": "BrandNewPass123!"},
        )
        assert change.status_code == 204, change.text

        app_client.cookies.clear()
        app_client.cookies.set("refresh_token", other_session_token)
        stale = await app_client.post("/api/v1/auth/refresh")
        assert stale.status_code == 401

        app_client.cookies.clear()
        app_client.cookies.set("refresh_token", current_session_token)
        still_ok = await app_client.post("/api/v1/auth/refresh")
        assert still_ok.status_code == 200, still_ok.text

    async def test_admin_password_reset_ends_every_session_of_that_employee(
        self, app_client, admin_user, db_session
    ):
        job_role = await _make_job_role(db_session)
        await _login_admin(app_client)
        created = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id))
        employee_id, username = created.json()["id"], created.json()["username"]

        employee_token = await _login(app_client, username, "TeacherPass123!")
        await _login_admin(app_client)

        reset = await app_client.post(
            f"/api/v1/employees/{employee_id}/reset-password", json={"new_password": "ResetByAdmin123!"}
        )
        assert reset.status_code == 204, reset.text

        app_client.cookies.clear()
        app_client.cookies.set("refresh_token", employee_token)
        resp = await app_client.post("/api/v1/auth/refresh")
        assert resp.status_code == 401