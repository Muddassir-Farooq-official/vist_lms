from __future__ import annotations

import pytest

from app.programs.models import Program

pytestmark = pytest.mark.asyncio


def _valid_student_payload(**overrides):
    payload = {
        "first_name": "Manual",
        "last_name": "Student",
        "date_of_birth": "2005-01-01",
        "gender": "Male",
        "cnic": "44444-4444444-4",
        "contact_no": "03004441111",
        "marital_status": "Single",
        "religion": "Islam",
        "nationality": "Pakistani",
        "guardian_name": "Guardian Name",
        "guardian_cnic": "44444-3333333-3",
        "relation_with_guardian": "Father",
        "emergency_contact_no": "03004442222",
        "present_address": "Some Address",
        "province": "Punjab",
        "district": "Lahore",
        "city": "Lahore",
    }
    payload.update(overrides)
    return payload


async def test_manual_create_student_success_and_can_login(app_client, admin_user):
    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})

    resp = await app_client.post("/api/v1/students", json=_valid_student_payload())
    assert resp.status_code == 201
    body = resp.json()
    # Student usernames are the system-generated roll number now
    # (<2-digit-year><seq>, e.g. "26001" for the 1st admission of 2026 —
    # see app/core/sequences.py::generate_roll_number), not a
    # CNIC-derived std-XXXXX code. First admission in a fresh test DB is
    # always seq 1.
    assert body["username"] == "26001"

    login_resp = await app_client.post(
        "/api/v1/auth/login", json={"username": body["username"], "password": "vertex123"}
    )
    assert login_resp.status_code == 200


async def test_manual_create_invalid_cnic_returns_422_not_500(app_client, admin_user):
    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})
    resp = await app_client.post("/api/v1/students", json=_valid_student_payload(cnic="not-a-cnic"))
    assert resp.status_code == 422


async def test_manual_create_invalid_gender_returns_422(app_client, admin_user):
    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})
    resp = await app_client.post("/api/v1/students", json=_valid_student_payload(gender="Alien"))
    assert resp.status_code == 422


async def test_manual_create_duplicate_cnic_rejected(app_client, admin_user):
    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})
    payload = _valid_student_payload()
    first = await app_client.post("/api/v1/students", json=payload)
    assert first.status_code == 201

    second = await app_client.post("/api/v1/students", json={**payload, "roll_number": "DIFFERENT"})
    assert second.status_code == 409


async def test_manual_create_with_program_id(app_client, admin_user, db_session):
    program = Program(code="BSN", name="BS Nursing", duration_years=4, total_semesters=8)
    db_session.add(program)
    await db_session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})
    resp = await app_client.post("/api/v1/students", json=_valid_student_payload(program_id=str(program.id)))
    assert resp.status_code == 201


async def test_manual_create_nonexistent_program_rejected(app_client, admin_user):
    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})
    resp = await app_client.post(
        "/api/v1/students", json=_valid_student_payload(program_id="00000000-0000-0000-0000-000000000000")
    )
    assert resp.status_code == 404


async def test_password_reset_sets_default_password(app_client, admin_user):
    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})
    create_resp = await app_client.post("/api/v1/students", json=_valid_student_payload())
    student_id = create_resp.json()["id"]
    username = create_resp.json()["username"]

    # Change the student's password away from the default first.
    student_login = await app_client.post(
        "/api/v1/auth/login", json={"username": username, "password": "vertex123"}
    )
    assert student_login.status_code == 200
    await app_client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "vertex123", "new_password": "SomethingElse123!"},
    )

    # Back to admin to trigger the reset.
    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})
    reset_resp = await app_client.post(f"/api/v1/students/{student_id}/reset-password")
    assert reset_resp.status_code == 204

    # Student can log in with the default password again.
    relogin = await app_client.post("/api/v1/auth/login", json={"username": username, "password": "vertex123"})
    assert relogin.status_code == 200
    assert relogin.json() or True  # login succeeded


async def test_non_admin_cannot_reset_password(app_client, db_session, seeded_roles):
    from app.auth.models import User, UserRole
    from app.auth.permissions import Roles
    from app.core.security import hash_password

    teacher = User(username="teacher_x", password_hash=hash_password("TeachPass123!"), must_change_password=False)
    db_session.add(teacher)
    await db_session.flush()
    db_session.add(UserRole(user_id=teacher.id, role_id=seeded_roles[Roles.TEACHER].id))
    await db_session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "teacher_x", "password": "TeachPass123!"})
    resp = await app_client.post("/api/v1/students/00000000-0000-0000-0000-000000000000/reset-password")
    assert resp.status_code == 403


async def test_csv_template_download(app_client, admin_user):
    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})
    resp = await app_client.get("/api/v1/imports/students/template")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "first_name" in resp.text
    assert "attachment" in resp.headers.get("content-disposition", "")