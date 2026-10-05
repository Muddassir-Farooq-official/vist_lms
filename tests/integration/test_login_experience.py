"""Sign-in experience: one page for every role, server-decided landing page,
no stale must_change_password token after a password change, fast static files."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


@pytest.fixture(autouse=True)
def _no_login_throttle(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def test_login_response_tells_the_browser_where_to_go(app_client, admin_user):
    r = await app_client.post("/api/v1/auth/login", json=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["username"] == "admin_test" and "ADMIN" in body["roles"]
    assert body["redirect_to"] == "/dashboard/admin"
    assert body["must_change_password"] is False
    assert isinstance(body["permissions"], list) and body["permissions"] and body["user_id"]


async def test_new_account_is_sent_to_change_password_first(app_client, admin_user):
    await app_client.post("/api/v1/auth/login", json=ADMIN)
    created = await app_client.post("/api/v1/students", json={
        "first_name": "Sana", "last_name": "Iqbal", "date_of_birth": "2005-03-03", "gender": "Female",
        "cnic": "35202-1111111-1", "contact_no": "03001110000", "marital_status": "Single", "religion": "Islam",
        "nationality": "Pakistani", "guardian_name": "G", "guardian_cnic": "35202-2222222-2",
        "relation_with_guardian": "Father", "emergency_contact_no": "03001110001", "present_address": "x",
        "province": "Punjab", "district": "Lahore", "city": "Lahore"})
    username = created.json()["username"]
    login = await app_client.post("/api/v1/auth/login", json={"username": username, "password": "vertex123"})
    assert login.json()["redirect_to"] == "/change-password" and login.json()["must_change_password"] is True


async def test_password_change_reissues_the_access_cookie(app_client, admin_user):
    """Regression: the old access token kept saying must_change_password=true, so a
    new student was bounced back to the change form right after changing it."""
    await app_client.post("/api/v1/auth/login", json=ADMIN)
    created = await app_client.post("/api/v1/students", json={
        "first_name": "Ali", "last_name": "Raza", "date_of_birth": "2005-03-03", "gender": "Male",
        "cnic": "35202-3333333-3", "contact_no": "03001110002", "marital_status": "Single", "religion": "Islam",
        "nationality": "Pakistani", "guardian_name": "G", "guardian_cnic": "35202-4444444-4",
        "relation_with_guardian": "Father", "emergency_contact_no": "03001110003", "present_address": "x",
        "province": "Punjab", "district": "Lahore", "city": "Lahore"})
    username = created.json()["username"]
    await app_client.post("/api/v1/auth/login", json={"username": username, "password": "vertex123"})
    assert (await app_client.get("/api/v1/auth/me")).json()["must_change_password"] is True

    ch = await app_client.post("/api/v1/auth/change-password",
                               json={"current_password": "vertex123", "new_password": "BrandNew#Pass2026"})
    assert ch.status_code == 204
    assert "access_token=" in ch.headers.get("set-cookie", "")
    me = (await app_client.get("/api/v1/auth/me")).json()
    assert me["must_change_password"] is False
    # and a protected endpoint now works in the SAME session, without signing in again
    assert (await app_client.get("/api/v1/academics/enrollments/me")).status_code != 403


async def test_root_is_the_single_sign_in_page(app_client):
    r = await app_client.get("/")
    assert r.status_code == 200 and 'id="login-form"' in r.text
    assert "Administrator" not in r.text and "coming soon" not in r.text.lower()
    assert r.headers["cache-control"] == "no-store"


async def test_signed_in_visitor_skips_the_form_but_reauth_shows_it(app_client, admin_user):
    await app_client.post("/api/v1/auth/login", json=ADMIN)
    skip = await app_client.get("/", follow_redirects=False)
    assert skip.status_code == 302 and skip.headers["location"] == "/dashboard/admin"
    forced = await app_client.get("/?reauth=1", follow_redirects=False)
    assert forced.status_code == 200 and 'id="login-form"' in forced.text  # no redirect loop possible


async def test_old_role_login_links_still_work(app_client):
    for slug in ("admin", "teacher", "student", "library", "accounts_officer", "whatever"):
        r = await app_client.get(f"/login/{slug}", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/?reauth=1"


async def test_static_files_are_cached_and_compressed(app_client):
    r = await app_client.get("/static/css/dashboard.css", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert "max-age=600" in r.headers["cache-control"]
    assert r.headers.get("content-encoding") == "gzip"


async def test_wrong_password_message_is_plain_and_does_not_leak_which_part_failed(app_client, admin_user):
    bad_pw = await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "nope-nope"})
    no_user = await app_client.post("/api/v1/auth/login", json={"username": "ghost", "password": "nope-nope"})
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json()["error"]["message"] == no_user.json()["error"]["message"] == "Invalid username or password."