"""Every kind of user can change their own password whenever they like (not only at first login)."""
from __future__ import annotations

import pytest

from app.employees.models import JobRole

pytestmark = pytest.mark.asyncio
ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


@pytest.fixture(autouse=True)
def _no_login_throttle(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _login(client, username, password):
    return await client.post("/api/v1/auth/login", json={"username": username, "password": password})


async def _make_staff(client, db, code, system_role, cnic):
    jr = JobRole(code=code, title=code, default_role_code=system_role)
    db.add(jr)
    await db.commit()
    r = await client.post("/api/v1/employees", json={
        "first_name": code, "last_name": "X", "date_of_birth": "1985-01-01", "gender": "Male", "cnic": cnic,
        "contact_no": "03007770000", "hire_date": "2026-01-01", "job_role_id": str(jr.id), "base_salary": 1,
        "password": "FirstPass#12345"})
    assert r.status_code == 201, r.text
    return r.json()["username"], "FirstPass#12345"


async def _make_student(client):
    r = await client.post("/api/v1/students", json={
        "first_name": "Sana", "last_name": "Iqbal", "date_of_birth": "2005-03-03", "gender": "Female",
        "cnic": "35202-1111111-1", "contact_no": "03001110000", "marital_status": "Single", "religion": "Islam",
        "nationality": "Pakistani", "guardian_name": "G", "guardian_cnic": "35202-2222222-2",
        "relation_with_guardian": "Father", "emergency_contact_no": "03001110001", "present_address": "x",
        "province": "Punjab", "district": "Lahore", "city": "Lahore"})
    assert r.status_code == 201, r.text
    return r.json()["username"], "vertex123"


async def _change(client, current, new):
    return await client.post("/api/v1/auth/change-password", json={"current_password": current, "new_password": new})


async def test_student_teacher_and_librarian_can_change_their_password_again_and_again(app_client, admin_user, db_session):
    await _login(app_client, **ADMIN)
    people = [await _make_student(app_client)]
    people.append(await _make_staff(app_client, db_session, "TCH", "TEACHER", "35202-3000001-1"))
    people.append(await _make_staff(app_client, db_session, "LIB", "LIBRARIAN", "35202-3000002-2"))
    for username, first in people:
        assert (await _login(app_client, username, first)).status_code == 200
        assert (await _change(app_client, first, "Second#Password1")).status_code == 204          # forced first change
        # later, of their own accord (this used to be impossible for everyone except the admin):
        assert (await _change(app_client, "Second#Password1", "Third#Password22")).status_code == 204
        assert (await _login(app_client, username, first)).status_code == 401                        # old passwords are dead
        assert (await _login(app_client, username, "Second#Password1")).status_code == 401
        assert (await _login(app_client, username, "Third#Password22")).status_code == 200
        me = (await app_client.get("/api/v1/auth/me")).json()
        assert me["must_change_password"] is False


async def test_bad_password_changes_are_refused_with_a_plain_reason(app_client, admin_user, db_session):
    await _login(app_client, **ADMIN)
    username, first = await _make_student(app_client)
    await _login(app_client, username, first)
    assert (await _change(app_client, first, "Brand#New#Pass1")).status_code == 204
    wrong = await _change(app_client, "not-my-password", "Another#Pass123")
    assert wrong.status_code == 401 and "incorrect" in wrong.json()["error"]["message"].lower()
    same = await _change(app_client, "Brand#New#Pass1", "Brand#New#Pass1")
    assert same.status_code == 422 and "different" in same.json()["error"]["message"]
    for weak in ("Vertex123", "vertex123"):                          # the well-known default, in any case (and too short anyway)
        assert (await _change(app_client, "Brand#New#Pass1", weak)).status_code == 422
    short = await _change(app_client, "Brand#New#Pass1", "short")
    assert short.status_code == 422


async def test_using_your_own_username_as_the_password_is_refused(app_client, admin_user, db_session):
    await _login(app_client, **ADMIN)
    username, first = await _make_staff(app_client, db_session, "UNM", "TEACHER", "35202-3000009-9")
    await _login(app_client, username, first)
    r = await _change(app_client, first, username)                  # employee codes are 10+ chars
    assert r.status_code == 422 and ("guess" in r.text or "at least" in r.text.lower() or "String should have" in r.text)


async def test_every_portal_menu_links_to_change_password_and_the_page_supports_voluntary_use(app_client):
    for path in ("/dashboard/admin", "/dashboard/faculty", "/dashboard/student", "/dashboard/library", "/dashboard/accounts/fees"):
        html = (await app_client.get(path)).text
        assert 'id="change-password-link"' in html and "/change-password?from=menu" in html, path
    page = (await app_client.get("/change-password?from=menu")).text
    assert 'id="cp-cancel-wrap"' in page and 'id="change-password-form"' in page
    js = (await app_client.get("/static/js/forced-change-password.js")).text
    assert 'get("from") === "menu"' in js