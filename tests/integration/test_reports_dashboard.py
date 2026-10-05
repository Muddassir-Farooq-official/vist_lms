from __future__ import annotations

from datetime import date

import pytest

from app.auth.models import User, UserRole
from app.auth.permissions import Roles
from app.core.security import hash_password
from app.programs.models import Program
from app.students.models import Student, StudentStatus

pytestmark = pytest.mark.asyncio


async def test_admin_dashboard_requires_auth(app_client):
    resp = await app_client.get("/api/v1/reports/admin-dashboard")
    assert resp.status_code == 401


async def test_admin_dashboard_rejects_non_admin(app_client, db_session, seeded_roles):
    user = User(username="teacher1", password_hash=hash_password("TeachPass123!"), must_change_password=False)
    db_session.add(user)
    await db_session.flush()
    db_session.add(UserRole(user_id=user.id, role_id=seeded_roles[Roles.TEACHER].id))
    await db_session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "teacher1", "password": "TeachPass123!"})
    resp = await app_client.get("/api/v1/reports/admin-dashboard")
    assert resp.status_code == 403


async def test_admin_dashboard_returns_correct_aggregates(app_client, db_session, admin_user):
    program = Program(code="BSN", name="BS Nursing", duration_years=4, total_semesters=8)
    db_session.add(program)
    await db_session.flush()

    active = Student(
        program_id=program.id, first_name="A", last_name="One", date_of_birth=date(2000, 1, 1),
        gender="Female", cnic="11111-1111111-1", contact_no="03001112222", marital_status="Single",
        religion="Islam", nationality="Pakistani", status=StudentStatus.ACTIVE, roll_number="RPT-01",
    )
    suspended = Student(
        program_id=program.id, first_name="B", last_name="Two", date_of_birth=date(2000, 1, 1),
        gender="Male", cnic="22222-2222222-2", contact_no="03001112223", marital_status="Single",
        religion="Islam", nationality="Pakistani", status=StudentStatus.SUSPENDED, roll_number="RPT-02",
    )
    db_session.add_all([active, suspended])
    await db_session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})
    resp = await app_client.get("/api/v1/reports/admin-dashboard")
    assert resp.status_code == 200

    body = resp.json()
    assert body["student_status_counts"]["active"] == 1
    assert body["student_status_counts"]["suspended"] == 1
    assert body["student_status_counts"]["total"] == 2
    assert body["total_programs"] == 1
    assert body["students_by_program"][0]["student_count"] == 2