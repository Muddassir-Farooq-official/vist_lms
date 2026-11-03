"""Activity log -> Security: refused and attack-looking attempts are recorded, and administrators are alerted."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.notifications.models import Notification
from app.security import service
from app.security.models import SecurityEvent

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)


def test_scanner_spots_attacks_and_leaves_normal_text_alone():
    assert service.scan(["x' OR '1'='1"])[0] == "SQL injection"
    assert service.scan(["1 UNION SELECT username, password_hash FROM users"])[0] == "SQL injection"
    assert service.scan(["<script>alert(document.cookie)</script>"])[0] == "Script injection (XSS)"
    assert service.scan(['<img src=x onerror="steal()">'])[0] == "Script injection (XSS)"
    assert service.scan(["../../../etc/passwd"])[0] == "Path traversal"
    assert service.scan(["%3Cscript%3Ealert(1)%3C%2Fscript%3E"])[0] == "Script injection (XSS)"
    for normal in ["Muhammad O'Brien", "Fee for Spring 2026 - second installment", "Fire alert (drill) on Monday",
                   "Rock 'n' roll club", "Select the union representative", "Students and teachers = everyone"]:
        assert service.scan([normal]) is None, normal
    # passwords are never looked at
    assert service.body_texts(b'{"username": "a", "password": "<script>x</script>"}', "application/json") == ["a"]
    assert service.looks_like_probe("/.env") and service.looks_like_probe("/wp-login.php") and not service.looks_like_probe("/dashboard/admin")


async def _count(db, kind):
    return (await db.execute(select(func.count()).select_from(SecurityEvent).where(SecurityEvent.kind == kind))).scalar_one()


async def test_attack_input_and_scans_are_recorded_and_alert_admins(app_client, admin_user, db_session):
    for i in range(3):
        await app_client.get("/api/v1/students", params={"q": f"x' OR '1'='{i}"})
    assert await _count(db_session, "SUSPICIOUS_INPUT") == 3
    row = (await db_session.execute(select(SecurityEvent).where(SecurityEvent.kind == "SUSPICIOUS_INPUT"))).scalars().first()
    assert row.ip_address is not None and "SQL injection" in row.detail
    notes = (await db_session.execute(select(Notification).where(Notification.user_id == admin_user.id))).scalars().all()
    assert any(n.title.startswith("Security alert") for n in notes)

    await app_client.get("/.env")
    await app_client.get("/wp-login.php")
    assert await _count(db_session, "PROBE") == 2


async def test_forged_token_and_refused_actions(app_client, admin_user, db_session):
    from tests.integration.test_fees import _create_and_activate_student

    app_client.cookies.set("access_token", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.forged")
    assert (await app_client.get("/api/v1/auth/me")).status_code == 401
    assert await _count(db_session, "BAD_TOKEN") == 1
    app_client.cookies.clear()

    sid, student_user = await _create_and_activate_student(app_client, cnic="72001-0000099-1")
    # signed in as the student: admin-only screens are refused and recorded, once per page per ten minutes
    for path in ("/api/v1/employees", "/api/v1/employees", "/api/v1/security/summary"):
        assert (await app_client.get(path)).status_code == 403, path
    rows = (await db_session.execute(select(SecurityEvent).where(SecurityEvent.kind == "ACCESS_DENIED"))).scalars().all()
    assert len(rows) == 2 and all(r.username for r in rows)   # (the account link needs a committed account; tests do not commit)


async def test_wrong_passwords_alert_and_show_on_the_security_tab(app_client, admin_user, db_session):
    for _ in range(service.FAILED_PER_ACCOUNT):
        r = await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "wrong-password-1"})
        assert r.status_code == 401
    notes = (await db_session.execute(select(Notification).where(Notification.user_id == admin_user.id))).scalars().all()
    assert any("wrong passwords for one account" in n.title for n in notes)

    from tests.integration.test_fees import _login_admin

    await _login_admin(app_client)
    s = (await app_client.get("/api/v1/security/summary", params={"days": 1})).json()
    assert s["failed_signins"] >= service.FAILED_PER_ACCOUNT and s["top_accounts"][0]["username"] == "admin_test"
    ev = (await app_client.get("/api/v1/security/events", params={"days": 1, "kind": "FAILED_SIGNIN"})).json()
    assert len(ev) >= service.FAILED_PER_ACCOUNT and ev[0]["ip"]