from __future__ import annotations

import pytest

from app.employees.models import JobRole

pytestmark = pytest.mark.asyncio


async def _login_admin(app_client):
    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})


async def _make_job_role(db_session, code="TCH", title="Teacher", default_role_code="TEACHER") -> JobRole:
    role = JobRole(code=code, title=title, default_role_code=default_role_code)
    db_session.add(role)
    await db_session.commit()
    return role


def _employee_payload(job_role_id, **overrides):
    payload = {
        "first_name": "Ali",
        "last_name": "Raza",
        "date_of_birth": "1990-05-01",
        "gender": "Male",
        "cnic": "11122-3334445-5",
        "contact_no": "03001112233",
        "hire_date": "2026-08-01",
        "department": "Computer Science",
        "job_role_id": str(job_role_id),
        "base_salary": 50000,
        "password": "TeacherPass123!",
    }
    payload.update(overrides)
    return payload


async def test_create_job_role_and_reject_invalid_default_role(app_client, admin_user):
    await _login_admin(app_client)
    resp = await app_client.post(
        "/api/v1/job-roles", json={"code": "TCH", "title": "Teacher", "default_role_code": "TEACHER"}
    )
    assert resp.status_code == 201

    bad = await app_client.post(
        "/api/v1/job-roles", json={"code": "BAD", "title": "Bad", "default_role_code": "NOT_REAL"}
    )
    assert bad.status_code == 422


async def test_create_employee_assigns_role_from_job_role_and_can_login(app_client, admin_user, db_session):
    job_role = await _make_job_role(db_session)
    await _login_admin(app_client)

    resp = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id))
    assert resp.status_code == 201
    body = resp.json()
    # Employee usernames are a system-generated sequential EMP-NNNN code
    # now (app/core/sequences.py::generate_employee_id), not derived from
    # CNIC — see migration c7f3a1d95e6b ("system-generated roll numbers /
    # employee IDs as login usernames").
    assert body["username"] == "EMP-0001"

    login = await app_client.post(
        "/api/v1/auth/login", json={"username": body["username"], "password": "TeacherPass123!"}
    )
    assert login.status_code == 200

    me = await app_client.get("/api/v1/auth/me")
    assert me.json()["roles"] == ["TEACHER"]


async def test_duplicate_employee_cnic_rejected(app_client, admin_user, db_session):
    job_role = await _make_job_role(db_session)
    await _login_admin(app_client)
    first = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id))
    assert first.status_code == 201

    second = await app_client.post(
        "/api/v1/employees", json=_employee_payload(job_role.id, contact_no="03009998888")
    )
    assert second.status_code == 409


async def test_non_admin_cannot_create_employee(app_client, db_session, seeded_roles):
    from app.auth.models import User, UserRole
    from app.auth.permissions import Roles
    from app.core.security import hash_password

    job_role = await _make_job_role(db_session)
    teacher_user = User(username="teacher_z", password_hash=hash_password("TeachPass123!"), must_change_password=False)
    db_session.add(teacher_user)
    await db_session.flush()
    db_session.add(UserRole(user_id=teacher_user.id, role_id=seeded_roles[Roles.TEACHER].id))
    await db_session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "teacher_z", "password": "TeachPass123!"})
    resp = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id))
    assert resp.status_code == 403


async def test_salary_increment_percentage_and_fixed_amount(app_client, admin_user, db_session):
    job_role = await _make_job_role(db_session)
    await _login_admin(app_client)
    create_resp = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id))
    employee_id = create_resp.json()["id"]

    pct = await app_client.post(
        f"/api/v1/employees/{employee_id}/salary-increment",
        json={"change_type": "PERCENTAGE", "value": 10, "reason": "Annual review"},
    )
    assert pct.status_code == 200
    assert pct.json()["base_salary"] == 55000.0

    fixed = await app_client.post(
        f"/api/v1/employees/{employee_id}/salary-increment",
        json={"change_type": "FIXED_AMOUNT", "value": 2000, "reason": "Adjustment"},
    )
    assert fixed.status_code == 200
    assert fixed.json()["base_salary"] == 57000.0

    history = await app_client.get(f"/api/v1/employees/{employee_id}/salary-history")
    rows = history.json()
    assert len(rows) == 3
    # exactly one row should be "current" (effective_to is null)
    current_rows = [r for r in rows if r["effective_to"] is None]
    assert len(current_rows) == 1
    assert current_rows[0]["base_salary"] == 57000.0


async def test_award_bonus(app_client, admin_user, db_session):
    job_role = await _make_job_role(db_session)
    await _login_admin(app_client)
    create_resp = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id))
    employee_id = create_resp.json()["id"]

    resp = await app_client.post(
        f"/api/v1/employees/{employee_id}/bonuses",
        json={"amount": 5000, "reason": "Eid bonus", "awarded_at": "2026-08-15"},
    )
    assert resp.status_code == 201
    assert resp.json()["amount"] == 5000.0
    assert resp.json()["reason"] == "Eid bonus"


async def test_employee_status_transitions(app_client, admin_user, db_session):
    job_role = await _make_job_role(db_session)
    await _login_admin(app_client)
    create_resp = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id))
    employee_id = create_resp.json()["id"]

    on_leave = await app_client.post(
        f"/api/v1/employees/{employee_id}/status", json={"new_status": "ON_LEAVE", "reason": "Personal"}
    )
    assert on_leave.status_code == 200
    assert on_leave.json()["status"] == "ON_LEAVE"

    terminated = await app_client.post(
        f"/api/v1/employees/{employee_id}/status", json={"new_status": "TERMINATED"}
    )
    assert terminated.status_code == 200

    # Terminated is terminal — no further transitions allowed.
    reactivate = await app_client.post(
        f"/api/v1/employees/{employee_id}/status", json={"new_status": "ACTIVE"}
    )
    assert reactivate.status_code == 422