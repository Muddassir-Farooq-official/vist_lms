from __future__ import annotations

import time
import uuid

import jwt
import pytest

from app.core.config import settings
from app.core.security import create_access_token

pytestmark = pytest.mark.asyncio


async def test_sql_injection_in_login_username_rejected_safely(app_client, seeded_roles):
    """SQLAlchemy's parameterized queries should neutralize this outright —
    confirms no raw string interpolation crept into the auth path."""
    payload = {"username": "admin' OR '1'='1", "password": "irrelevant"}
    resp = await app_client.post("/api/v1/auth/login", json=payload)
    assert resp.status_code == 401  # rejected as invalid credentials, not a 500/SQL error
    assert resp.json()["error"]["code"] == "AUTHENTICATION_ERROR"


async def test_sql_injection_in_student_search_query_param(app_client, admin_user):
    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})
    resp = await app_client.get("/api/v1/students", params={"q": "'; DROP TABLE students; --"})
    assert resp.status_code == 200  # treated as a literal search string, not executed
    assert resp.json()["items"] == []


async def test_tampered_jwt_signature_rejected(app_client):
    fake_token = create_access_token(
        user_id=uuid.uuid4(),
        username="attacker",
        roles=["ADMIN"],
        permissions=["student.import", "program.delete"],
        must_change_password=False,
    )
    tampered = fake_token[:-4] + "AAAA"  # corrupt the signature
    resp = await app_client.get("/api/v1/students", headers={"Authorization": f"Bearer {tampered}"})
    assert resp.status_code == 401


async def test_expired_jwt_rejected(app_client):
    now = time.time()
    payload = {
        "sub": str(uuid.uuid4()),
        "username": "someone",
        "roles": ["ADMIN"],
        "perms": ["student.view"],
        "must_change_password": False,
        "type": "access",
        "iat": now - 3600,
        "exp": now - 1800,  # expired 30 minutes ago
        "jti": str(uuid.uuid4()),
    }
    expired_token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    resp = await app_client.get("/api/v1/students", headers={"Authorization": f"Bearer {expired_token}"})
    assert resp.status_code == 401


async def test_token_with_wrong_secret_rejected(app_client):
    payload = {
        "sub": str(uuid.uuid4()),
        "username": "attacker",
        "roles": ["ADMIN"],
        "perms": ["student.import"],
        "must_change_password": False,
        "type": "access",
        "iat": time.time(),
        "exp": time.time() + 900,
        "jti": str(uuid.uuid4()),
    }
    forged_token = jwt.encode(payload, "not-the-real-secret", algorithm=settings.jwt_algorithm)
    resp = await app_client.get("/api/v1/students", headers={"Authorization": f"Bearer {forged_token}"})
    assert resp.status_code == 401


async def test_refresh_token_type_cannot_be_used_as_access_token(app_client):
    """A token minted with type='refresh' must not be accepted where an
    access token is expected — guards against token-type confusion."""
    now = time.time()
    payload = {
        "sub": str(uuid.uuid4()),
        "username": "someone",
        "roles": ["ADMIN"],
        "perms": ["student.import"],
        "must_change_password": False,
        "type": "refresh",  # wrong type
        "iat": now,
        "exp": now + 900,
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    resp = await app_client.get("/api/v1/students", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_oversized_csv_upload_rejected(app_client, admin_user):
    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})
    oversized = b"first_name,last_name\n" + (b"a" * (settings.import_max_file_size_mb * 1024 * 1024 + 1))
    resp = await app_client.post(
        "/api/v1/imports/students",
        files={"file": ("huge.csv", oversized, "text/csv")},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_non_csv_extension_rejected(app_client, admin_user):
    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})
    resp = await app_client.post(
        "/api/v1/imports/students",
        files={"file": ("students.exe", b"not a real csv", "application/octet-stream")},
    )
    assert resp.status_code == 422


async def test_empty_csv_upload_rejected(app_client, admin_user):
    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})
    resp = await app_client.post(
        "/api/v1/imports/students",
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert resp.status_code == 422


async def test_response_headers_include_security_headers(app_client):
    resp = await app_client.get("/health")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"


async def test_error_response_never_leaks_internals(app_client, admin_user):
    """A 500-triggering internal error must return the generic structured
    shape, never a raw traceback or SQL statement."""
    await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "AdminPass123!"})
    resp = await app_client.get("/api/v1/students/not-a-valid-uuid")
    # FastAPI's own path-param validation catches this before it reaches
    # the DB — still must not leak a stack trace either way.
    assert resp.status_code in (404, 422)
    body = resp.text
    assert "Traceback" not in body
    assert "sqlalchemy" not in body.lower()
