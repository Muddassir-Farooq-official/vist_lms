from __future__ import annotations

import pytest

from datetime import date

from app.auth.models import Role, User, UserRole
from app.auth.permissions import Roles
from app.core.security import hash_password
from app.students.models import Student, StudentStatus

pytestmark = pytest.mark.asyncio


async def test_login_success_sets_cookies(app_client, admin_user):
    resp = await app_client.post(
        "/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"}
    )
    assert resp.status_code == 200
    assert "access_token" in resp.cookies
    assert "refresh_token" in resp.cookies
    body = resp.json()
    assert body["access_token"]


async def test_login_wrong_password_rejected(app_client, admin_user):
    resp = await app_client.post(
        "/api/v1/auth/login", json={"username": "admin_test", "password": "WrongPassword!"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTHENTICATION_ERROR"


async def test_login_unknown_user_rejected(app_client, seeded_roles):
    resp = await app_client.post(
        "/api/v1/auth/login", json={"username": "nobody", "password": "whatever123"}
    )
    assert resp.status_code == 401


async def test_login_rate_limited_after_repeated_failures(app_client, admin_user):
    for _ in range(5):
        await app_client.post(
            "/api/v1/auth/login", json={"username": "admin_test", "password": "WrongPassword!"}
        )
    resp = await app_client.post(
        "/api/v1/auth/login", json={"username": "admin_test", "password": "WrongPassword!"}
    )
    assert resp.status_code == 429


async def test_protected_endpoint_rejects_no_auth(app_client):
    resp = await app_client.get("/api/v1/students")
    assert resp.status_code == 401


async def test_protected_endpoint_rejects_garbage_token(app_client):
    resp = await app_client.get("/api/v1/students", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert resp.status_code == 401


async def test_admin_can_create_program(app_client, admin_user):
    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})
    resp = await app_client.post(
        "/api/v1/programs",
        json={"code": "BSN", "name": "BS Nursing", "duration_years": 4, "total_semesters": 8},
    )
    assert resp.status_code == 201
    assert resp.json()["code"] == "BSN"


async def test_student_role_forbidden_from_program_create(app_client, db_session, seeded_roles):
    student_user = User(username="stud1", password_hash=hash_password("StudPass123!"), must_change_password=False)
    db_session.add(student_user)
    await db_session.flush()
    db_session.add(UserRole(user_id=student_user.id, role_id=seeded_roles[Roles.STUDENT].id))
    await db_session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "stud1", "password": "StudPass123!"})
    resp = await app_client.post(
        "/api/v1/programs",
        json={"code": "BSN", "name": "BS Nursing", "duration_years": 4, "total_semesters": 8},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTHORIZATION_ERROR"


async def test_student_cannot_view_another_students_record(app_client, db_session, seeded_roles):
    # Two students, each with their own linked user account.
    user_a = User(username="student_a", password_hash=hash_password("PassA123!"), must_change_password=False)
    user_b = User(username="student_b", password_hash=hash_password("PassB123!"), must_change_password=False)
    db_session.add_all([user_a, user_b])
    await db_session.flush()
    db_session.add(UserRole(user_id=user_a.id, role_id=seeded_roles[Roles.STUDENT].id))
    db_session.add(UserRole(user_id=user_b.id, role_id=seeded_roles[Roles.STUDENT].id))

    student_b = Student(
        user_id=user_b.id,
        first_name="Bee",
        last_name="Student",
        date_of_birth=date(2000, 1, 1),
        gender="Female",
        cnic="11111-1111111-1",
        contact_no="03001112222",
        marital_status="Single",
        religion="Islam",
        nationality="Pakistani",
        roll_number="24901",  # direct ORM construction bypasses the
        # normal roll-number generator (app/core/sequences.py) — this
        # column has been NOT NULL since migration d1f5b8e6c3a4, so a
        # fixture built this way must supply one explicitly.
        status=StudentStatus.ACTIVE,
    )
    db_session.add(student_b)
    await db_session.commit()

    # Log in as student_a, try to view student_b's record directly by ID — IDOR check.
    await app_client.post("/api/v1/auth/login", json={"username": "student_a", "password": "PassA123!"})
    resp = await app_client.get(f"/api/v1/students/{student_b.id}")
    assert resp.status_code == 403


async def test_student_can_view_own_record_via_me_endpoint(app_client, db_session, seeded_roles):
    user_a = User(username="student_a2", password_hash=hash_password("PassA123!"), must_change_password=False)
    db_session.add(user_a)
    await db_session.flush()
    db_session.add(UserRole(user_id=user_a.id, role_id=seeded_roles[Roles.STUDENT].id))

    student_a = Student(
        user_id=user_a.id,
        first_name="Aye",
        last_name="Student",
        date_of_birth=date(2000, 1, 1),
        gender="Female",
        cnic="22222-2222222-2",
        contact_no="03001112223",
        marital_status="Single",
        religion="Islam",
        nationality="Pakistani",
        roll_number="24902",
        status=StudentStatus.ACTIVE,
    )
    db_session.add(student_a)
    await db_session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "student_a2", "password": "PassA123!"})
    resp = await app_client.get("/api/v1/students/me")
    assert resp.status_code == 200
    assert resp.json()["first_name"] == "Aye"


async def test_student_role_cannot_list_all_students(app_client, db_session, seeded_roles):
    user_a = User(username="student_search", password_hash=hash_password("PassA123!"), must_change_password=False)
    db_session.add(user_a)
    await db_session.flush()
    db_session.add(UserRole(user_id=user_a.id, role_id=seeded_roles[Roles.STUDENT].id))
    await db_session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "student_search", "password": "PassA123!"})
    resp = await app_client.get("/api/v1/students")
    assert resp.status_code == 403


async def test_health_endpoints_public(app_client):
    resp = await app_client.get("/health")
    assert resp.status_code == 200
    resp = await app_client.get("/health/live")
    assert resp.status_code == 200


async def test_login_rehashes_password_stored_with_weaker_hasher_parameters(
    app_client, db_session, seeded_roles
):
    """needs_rehash() existed in app/core/security.py but was never
    actually called anywhere — meaning even if the Argon2 parameters
    were tightened, no existing user's stored hash could ever have been
    upgraded. This constructs a hash with deliberately weaker-than-
    current parameters (simulating "this hash predates a parameter
    change") and confirms a successful login transparently replaces it,
    while the account's password functionally stays the same."""
    from argon2 import PasswordHasher

    weak_hasher = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    weak_hash = weak_hasher.hash("RehashMe123!")

    user = User(username="rehash_target", password_hash=weak_hash, must_change_password=False)
    db_session.add(user)
    await db_session.flush()
    db_session.add(UserRole(user_id=user.id, role_id=seeded_roles[Roles.STUDENT].id))
    await db_session.commit()

    login_resp = await app_client.post(
        "/api/v1/auth/login", json={"username": "rehash_target", "password": "RehashMe123!"}
    )
    assert login_resp.status_code == 200

    await db_session.refresh(user)
    assert user.password_hash != weak_hash  # replaced with a hash under the app's real parameters

    # The account must still be usable with the SAME plaintext password
    # afterward — rehashing must be invisible to the user.
    relogin_resp = await app_client.post(
        "/api/v1/auth/login", json={"username": "rehash_target", "password": "RehashMe123!"}
    )
    assert relogin_resp.status_code == 200


async def test_login_rate_limit_cannot_be_bypassed_by_rotating_source_ip(db_session, monkeypatch):
    """Regression test for the fix: the ORIGINAL rate limiter was keyed
    on (username, ip) only, so an attacker rotating source IPs against
    one username was never slowed at all — each new IP got its own
    fresh allowance. This drives failed attempts against ONE username
    from a DIFFERENT ip each time, staying comfortably under the per-IP
    cap on every single request, and confirms the aggregate is still
    capped by the new per-username-only limiter."""
    from app.auth.service import AuthService
    from app.core.config import settings
    from app.core.exceptions import AuthenticationError, RateLimitedError

    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 3)
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 100)  # keep the per-IP limiter out of the way

    service = AuthService(db_session)

    # First 3 attempts (global cap) each from a brand-new IP — none of
    # them would ever trip a per-IP-only limiter.
    for i in range(3):
        with pytest.raises(AuthenticationError):
            await service.login(
                username="rate_limit_target", password="wrong-password",
                ip=f"203.0.113.{i}", user_agent=None,
            )

    # The 4th attempt, from yet another brand-new IP, must still be
    # blocked — this is exactly the case the old per-(username, ip)-only
    # limiter could not catch.
    with pytest.raises(RateLimitedError):
        await service.login(
            username="rate_limit_target", password="wrong-password",
            ip="203.0.113.99", user_agent=None,
        )