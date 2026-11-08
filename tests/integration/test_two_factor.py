"""Two-step sign-in for administrators (app/auth/two_factor.py)."""
from __future__ import annotations

import time

import pytest
from sqlalchemy import select

from app.auth import two_factor as tf
from app.core.config import settings

pytestmark = pytest.mark.asyncio
LOGIN = "/api/v1/auth/login"
ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(settings, "two_factor_required_for_admins", 1)
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


@pytest.fixture(autouse=True)
async def _fresh_redis():
    from app.core.redis import get_redis

    r = get_redis()
    for pattern in ("2fa:*",):
        async for k in r.scan_iter(pattern):
            await r.delete(k)
    yield


def test_codes_follow_the_standard():
    # RFC 6238 test vector (SHA-1, 8 digits at T=59 is 94287082; the last 6 digits are what a 6-digit app shows)
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"                     # base32 of "12345678901234567890"
    assert tf._code_at(secret, 59 // 30) == "287082"
    assert tf._code_at(secret, 1111111109 // 30) == "081804"
    assert tf.matching_step(secret, "287 082", at=59) == 1
    assert tf.matching_step(secret, "287082", at=59 + 30) == 1            # one step late still works (slow phone clock)
    assert tf.matching_step(secret, "287082", at=59 + 90) is None
    assert tf.matching_step(secret, "12345", at=59) is None


async def _set_up(app_client, remember=False, db_session=None):
    if db_session is not None:
        db_session.expunge_all()                     # like a fresh request: nothing about the account is already loaded
    first = (await app_client.post(LOGIN, json=ADMIN)).json()
    assert first["two_factor"] == "setup" and first["access_token"] == "" and first["remember_days"] == 30
    setup = (await app_client.post("/api/v1/auth/2fa/setup", json={"challenge": first["challenge"]})).json()
    assert setup["qr_svg"].lstrip().startswith("<svg") and setup["account"] == "admin_test"
    secret = setup["key"].replace(" ", "")
    wrong = await app_client.post("/api/v1/auth/2fa/setup/confirm", json={"challenge": first["challenge"], "code": "000000"})
    assert wrong.status_code == 401
    ok = await app_client.post("/api/v1/auth/2fa/setup/confirm", json={"challenge": first["challenge"], "code": tf.code_now(secret), "remember": remember})
    assert ok.status_code == 200, ok.text
    return secret, ok


async def test_an_administrator_sets_it_up_once_and_then_types_a_code(app_client, admin_user, db_session):
    from app.audit.models import AuditLog

    secret, ok = await _set_up(app_client, db_session=db_session)
    assert ok.json()["access_token"] and ok.json()["redirect_to"].startswith("/dashboard/admin")
    assert (await app_client.get("/api/v1/auth/me")).status_code == 200
    await app_client.post("/api/v1/auth/logout")
    app_client.cookies.clear()

    second = (await app_client.post(LOGIN, json=ADMIN)).json()
    assert second["two_factor"] == "code" and not second["access_token"]
    assert (await app_client.get("/api/v1/auth/me")).status_code == 401              # nothing issued before the code
    db_session.expunge_all()
    bad = await app_client.post("/api/v1/auth/2fa/verify", json={"challenge": second["challenge"], "code": "123456"})
    assert bad.status_code == 401 and "not right" in bad.text
    code = tf.code_now(secret, time.time() + 30)                                       # the next code (the current one was used to set up)
    good = await app_client.post("/api/v1/auth/2fa/verify", json={"challenge": second["challenge"], "code": code})
    assert good.status_code == 200 and good.json()["access_token"]
    again = await app_client.post("/api/v1/auth/2fa/verify", json={"challenge": second["challenge"], "code": code})
    assert again.status_code == 401 and "just used" in again.text                    # a code works once
    actions = set((await db_session.execute(select(AuditLog.action))).scalars())
    assert {"auth.two_factor_enabled", "auth.two_factor_failed"} <= actions


async def test_a_remembered_device_skips_the_code_and_a_new_one_asks(app_client, admin_user, db_session):
    await _set_up(app_client, remember=True)
    assert app_client.cookies.get("vist_2fa_device")
    await app_client.post("/api/v1/auth/logout")
    for k in ("access_token", "refresh_token"):
        app_client.cookies.delete(k)
    back = (await app_client.post(LOGIN, json=ADMIN)).json()
    assert back["access_token"] and back["two_factor"] is None                        # same browser: straight in
    app_client.cookies.clear()
    elsewhere = (await app_client.post(LOGIN, json=ADMIN)).json()
    assert elsewhere["two_factor"] == "code"                                          # a new device always asks


async def test_too_many_wrong_codes_stop_further_tries(app_client, admin_user, db_session):
    secret, _ = await _set_up(app_client)
    app_client.cookies.clear()
    ch = (await app_client.post(LOGIN, json=ADMIN)).json()["challenge"]
    for _ in range(tf.MAX_CODE_TRIES):
        assert (await app_client.post("/api/v1/auth/2fa/verify", json={"challenge": ch, "code": "000001"})).status_code == 401
    blocked = await app_client.post("/api/v1/auth/2fa/verify", json={"challenge": ch, "code": tf.code_now(secret, time.time() + 30)})
    assert blocked.status_code == 429


async def test_a_challenge_is_not_a_sign_in_and_cannot_be_reused_for_the_other_step(app_client, admin_user, db_session):
    first = (await app_client.post(LOGIN, json=ADMIN)).json()
    r = await app_client.post("/api/v1/auth/2fa/verify", json={"challenge": first["challenge"], "code": "123456"})
    assert r.status_code == 401                                                       # a set-up challenge is not a code challenge
    r = await app_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {first['challenge']}"})
    assert r.status_code == 401


async def test_other_people_are_never_asked(app_client, admin_user, db_session):
    from tests.integration.test_fees import _create_and_activate_student, _login_admin  # noqa: F401
    from app.auth.models import User

    admin_user.totp_secret_enc = None
    monkey_off = settings.two_factor_required_for_admins
    settings.two_factor_required_for_admins = 0
    try:
        await _login_admin(app_client)
        sid, username = await _create_and_activate_student(app_client, cnic="72001-0000001-1")
    finally:
        settings.two_factor_required_for_admins = monkey_off
    app_client.cookies.clear()
    r = (await app_client.post(LOGIN, json={"username": username, "password": "StudentPass123!"})).json()
    assert r.get("two_factor") is None and r["access_token"]
    assert (await db_session.execute(select(User).where(User.username == username))).scalar_one().totp_secret_enc is None


async def test_another_administrator_resets_a_lost_phone_and_the_person_sets_it_up_again(app_client, admin_user, db_session, seeded_roles):
    from app.auth.models import User, UserRole
    from app.auth.permissions import Roles
    from app.core.security import hash_password

    await _set_up(app_client)
    other = User(username="admin_two", password_hash=hash_password("AdminTwo123!x"), must_change_password=False)
    db_session.add(other)
    await db_session.flush()
    db_session.add(UserRole(user_id=other.id, role_id=seeded_roles[Roles.ADMIN].id))
    await db_session.commit()

    me = (await app_client.get("/api/v1/security/two-factor")).json()
    rows = {a["username"]: a for a in me["accounts"]}
    assert me["required"] and rows["admin_test"]["set_up"] and not rows["admin_two"]["set_up"] and rows["admin_test"]["is_me"]
    own = await app_client.post(f"/api/v1/security/two-factor/{admin_user.id}/reset", json={"reason": "lost my phone"})
    assert own.status_code == 422                                                     # not your own

    # admin_two signs in (sets up their own), then resets admin_test
    app_client.cookies.clear()
    first = (await app_client.post(LOGIN, json={"username": "admin_two", "password": "AdminTwo123!x"})).json()
    key = (await app_client.post("/api/v1/auth/2fa/setup", json={"challenge": first["challenge"]})).json()["key"].replace(" ", "")
    assert (await app_client.post("/api/v1/auth/2fa/setup/confirm", json={"challenge": first["challenge"], "code": tf.code_now(key)})).status_code == 200
    r = await app_client.post(f"/api/v1/security/two-factor/{admin_user.id}/reset", json={"reason": "Phone stolen, confirmed in person"})
    assert r.status_code == 200 and r.json()["set_up"] is False

    app_client.cookies.clear()
    again = (await app_client.post(LOGIN, json=ADMIN)).json()
    assert again["two_factor"] == "setup"                                             # sets it up again


async def test_the_server_script_resets_when_no_other_administrator_can(app_client, admin_user, db_session, monkeypatch):
    from app.auth.models import User

    await _set_up(app_client)
    await db_session.refresh(admin_user)
    assert admin_user.totp_secret_enc is not None
    await tf.reset(db_session, admin_user, actor_id=None, how="reset on the server (scripts/reset_2fa.py)")
    await db_session.commit()
    u = (await db_session.execute(select(User).where(User.id == admin_user.id).execution_options(populate_existing=True))).scalar_one()
    assert u.totp_secret_enc is None and u.totp_enabled_at is None
    app_client.cookies.clear()
    assert (await app_client.post(LOGIN, json=ADMIN)).json()["two_factor"] == "setup"


async def test_turning_the_requirement_off_lets_administrators_in_with_the_password(app_client, admin_user, db_session, monkeypatch):
    await _set_up(app_client)
    app_client.cookies.clear()
    monkeypatch.setattr(settings, "two_factor_required_for_admins", 0)
    r = (await app_client.post(LOGIN, json=ADMIN)).json()
    assert r["access_token"] and r.get("two_factor") is None


def test_the_remembered_device_cookie_does_not_share_a_name_with_the_security_check():
    from app.security.middleware import DEVICE_COOKIE as SECURITY_COOKIE

    assert tf.DEVICE_COOKIE != SECURITY_COOKIE                  # same name: each would overwrite the other in the browser
