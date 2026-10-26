"""General staff (office boy, IT, driver, cleaner...): their own portal, only their own things, and nothing about students, fees or marks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.auth.redirects import home_for_roles, profile_for_roles
from app.employees.models import JobRole
from tests.integration.test_file_download_authorization import _employee_payload, _login, _login_admin

PASSWORD, NEW_PASSWORD = "TeacherPass123!", "StaffReal123!"


async def _create_staff(app_client, db_session, *, cnic: str) -> tuple[str, str]:
    job = JobRole(code="STAFF_T", title="Office Assistant", default_role_code="STAFF")
    db_session.add(job)
    await db_session.commit()
    await _login_admin(app_client)
    resp = await app_client.post("/api/v1/employees", json=_employee_payload(job.id, cnic=cnic))
    assert resp.status_code == 201, resp.text
    username = resp.json()["username"]
    await _login(app_client, username, PASSWORD)
    assert (await app_client.post("/api/v1/auth/change-password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})).status_code == 204
    await _login(app_client, username, NEW_PASSWORD)
    return resp.json()["id"], username


def test_staff_lands_on_the_staff_portal():
    assert home_for_roles(["STAFF"]) == "/dashboard/staff"
    assert profile_for_roles(["STAFF"]) == "/dashboard/staff/profile"
    assert profile_for_roles(["SECURITY_GUARD"]) == "/dashboard/gate/profile"


async def test_a_staff_member_has_their_own_things_only(app_client, admin_user, db_session):
    _eid, username = await _create_staff(app_client, db_session, cnic="73001-0000001-1")
    me = (await app_client.get("/api/v1/auth/me")).json()
    assert "STAFF" in me["roles"]
    assert set(me["permissions"]) == {"application.submit", "task.use", "purchase.request", "complaint.submit"}

    # Their own things work.
    for path in ("/api/v1/tasks/summary", "/api/v1/tasks/inbox", "/api/v1/applications/mine", "/api/v1/applications/types", "/api/v1/purchases/mine",
                 "/api/v1/payroll/me", "/api/v1/staff-attendance/me", "/api/v1/profile-changes/me", "/api/v1/announcements", "/api/v1/complaints/me"):
        assert (await app_client.get(path)).status_code == 200, path
    made = await app_client.post("/api/v1/purchases", json={"title": "Network cable", "justification": "For the lab point.",
                                                           "items": [{"description": "Cat6 50 m", "quantity": 1, "estimated_unit_price": 4500}]})
    assert made.status_code == 201, made.text

    # Students, fees, marks, payroll and other offices' data are refused.
    for path in ("/api/v1/students", "/api/v1/employees", "/api/v1/fees/structures", "/api/v1/payroll/runs", "/api/v1/hostel/summary",
                 "/api/v1/library/books", "/api/v1/audit/activity", "/api/v1/staff-attendance/board", "/api/v1/profile-changes"):
        assert (await app_client.get(path)).status_code == 403, path

    # Every page of the portal opens; the office's own profile pages are there too.
    for page in ("/dashboard/staff", "/dashboard/staff/tasks", "/dashboard/staff/payslips", "/dashboard/staff/announcements", "/dashboard/staff/applications",
                 "/dashboard/staff/purchases", "/dashboard/staff/complaints", "/dashboard/staff/calendar", "/dashboard/staff/profile",
                 "/dashboard/gate/profile", "/dashboard/accounts/profile", "/dashboard/library/profile", "/dashboard/exam/profile", "/dashboard/warden/profile"):
        assert (await app_client.get(page)).status_code == 200, page
    assert (await app_client.get("/dashboard/staff/nothing")).status_code == 404
    landing = await app_client.get("/", follow_redirects=False)
    assert landing.status_code == 302 and landing.headers["location"] == "/dashboard/staff"
    mine = await app_client.get("/dashboard/my-profile", follow_redirects=False)
    assert mine.headers["location"] == "/dashboard/staff/profile"

    # They can ask the office to change their phone number (same approval flow as everyone else).
    assert (await app_client.post("/api/v1/profile-changes/me", json={"values": {"contact_no": "0301-2223334"}})).status_code == 200


async def test_the_administration_can_give_tasks_to_staff(app_client, admin_user, db_session):
    _eid, username = await _create_staff(app_client, db_session, cnic="73002-0000001-1")
    await _login_admin(app_client)
    people = (await app_client.get("/api/v1/tasks/staff")).json()
    person = next(p for p in people if p.get("identifier") == username)
    deadline = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    task = await app_client.post("/api/v1/tasks", json={"assignee_user_id": person["user_id"], "title": "Fix the projector", "description": "Hall B", "deadline": deadline})
    assert task.status_code == 201, task.text
    await _login(app_client, username, NEW_PASSWORD)
    assert (await app_client.get("/api/v1/tasks/summary")).json()["needs_response"] == 1
    notes = (await app_client.get("/api/v1/notifications/me")).json()
    assert any((n.get("link") or "").startswith("/dashboard/staff/tasks") for n in notes)


async def test_staff_only_announcements_reach_staff_but_not_parents(app_client, admin_user, db_session):
    from app.announcements.service import AnnouncementService

    service = AnnouncementService(db_session)
    staff = await service.viewer_for(admin_user.id, ["STAFF"])
    parent = await service.viewer_for(admin_user.id, ["PARENT"])
    assert staff.staff and not parent.staff
