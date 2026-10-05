from __future__ import annotations

import pytest

from app.employees.models import JobRole

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


def _employee_payload(job_role_id, **overrides):
    payload = {
        "first_name": "Term", "last_name": "Inated",
        "date_of_birth": "1985-01-01", "gender": "Male",
        "cnic": "75001-0000001-1", "contact_no": "03007550000",
        "hire_date": "2026-08-01", "department": "Nursing",
        "job_role_id": str(job_role_id), "base_salary": 80000,
        "password": "TeacherPass123!",
    }
    payload.update(overrides)
    return payload


async def _login(app_client, username, password):
    resp = await app_client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


async def _login_admin(app_client):
    await _login(app_client, ADMIN["username"], ADMIN["password"])


async def _create_teacher(app_client, db_session, *, code, cnic) -> str:
    job_role = JobRole(code=code, title="Teacher", default_role_code="TEACHER")
    db_session.add(job_role)
    await db_session.commit()
    await _login_admin(app_client)
    resp = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id, cnic=cnic))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _create_course_offering(app_client, *, code, teacher_id=None, term_start_date=None, term_end_date=None) -> str:
    course_resp = await app_client.post(
        "/api/v1/academics/courses", json={"code": f"{code}-C1", "title": "Test Course", "credit_hours": 3}
    )
    assert course_resp.status_code == 201, course_resp.text
    course_id = course_resp.json()["id"]

    offering_resp = await app_client.post(
        "/api/v1/academics/offerings",
        json={
            "course_id": course_id, "term_label": "Fall 2026", "assigned_teacher_id": teacher_id,
            "term_start_date": term_start_date, "term_end_date": term_end_date,
        },
    )
    assert offering_resp.status_code == 201, offering_resp.text
    return offering_resp.json()["id"]


class TestTerminationUnassignsOpenOfferings:
    async def test_terminating_a_teacher_unassigns_them_from_a_still_open_offering(
        self, app_client, admin_user, db_session
    ):
        teacher_id = await _create_teacher(app_client, db_session, code="TERM1", cnic="76001-0000001-1")
        offering_id = await _create_course_offering(app_client, code="TERM1", teacher_id=teacher_id)
        open_resp = await app_client.patch(
            f"/api/v1/academics/offerings/{offering_id}", json={"is_open_for_enrollment": True}
        )
        assert open_resp.status_code == 200, open_resp.text

        status_resp = await app_client.post(
            f"/api/v1/employees/{teacher_id}/status", json={"new_status": "TERMINATED", "reason": "Resigned"}
        )
        assert status_resp.status_code == 200, status_resp.text
        assert status_resp.json()["status"] == "TERMINATED"

        offering_detail = await app_client.get(f"/api/v1/academics/offerings/{offering_id}")
        assert offering_detail.status_code == 200
        assert offering_detail.json()["assigned_teacher_id"] is None
        assert offering_detail.json()["teacher_name"] is None

    async def test_termination_is_never_blocked_by_open_offerings(self, app_client, admin_user, db_session):
        """Deliberately different from student graduation clearance —
        an HR termination must always go through regardless of open
        commitments; the system reconciles the data, it doesn't refuse
        the action."""
        teacher_id = await _create_teacher(app_client, db_session, code="TERM2", cnic="76002-0000001-1")
        await _create_course_offering(app_client, code="TERM2", teacher_id=teacher_id)
        await _create_course_offering(app_client, code="TERM2B", teacher_id=teacher_id)

        status_resp = await app_client.post(
            f"/api/v1/employees/{teacher_id}/status", json={"new_status": "TERMINATED", "reason": "Immediate dismissal"}
        )
        assert status_resp.status_code == 200, status_resp.text

    async def test_concluded_offerings_keep_the_terminated_teacher_as_historical_record(
        self, app_client, admin_user, db_session
    ):
        """A genuinely CONCLUDED offering — its term_end_date has
        already passed — must NOT have its teacher erased, since that
        would destroy the real historical record of who taught it."""
        teacher_id = await _create_teacher(app_client, db_session, code="TERM3", cnic="76003-0000001-1")
        past_offering_id = await _create_course_offering(
            app_client, code="TERM3", teacher_id=teacher_id,
            term_start_date="2020-01-01", term_end_date="2020-05-01",
        )

        status_resp = await app_client.post(
            f"/api/v1/employees/{teacher_id}/status", json={"new_status": "TERMINATED", "reason": "End of contract"}
        )
        assert status_resp.status_code == 200, status_resp.text

        offering_detail = await app_client.get(f"/api/v1/academics/offerings/{past_offering_id}")
        assert offering_detail.json()["assigned_teacher_id"] == teacher_id

    async def test_offering_with_no_term_dates_is_treated_as_still_active_not_historical(
        self, app_client, admin_user, db_session
    ):
        """Real bug this replaces: an offering that was simply never
        opened for enrollment yet (is_open_for_enrollment defaults
        False, same as a genuinely concluded one) used to be wrongly
        treated as historical and kept its terminated teacher forever.
        With no term_end_date to prove it's actually over, it must be
        unassigned like any other current/future offering."""
        teacher_id = await _create_teacher(app_client, db_session, code="TERM3B", cnic="76003-0000002-2")
        never_opened_offering_id = await _create_course_offering(app_client, code="TERM3B", teacher_id=teacher_id)

        status_resp = await app_client.post(
            f"/api/v1/employees/{teacher_id}/status", json={"new_status": "TERMINATED", "reason": "End of contract"}
        )
        assert status_resp.status_code == 200, status_resp.text

        offering_detail = await app_client.get(f"/api/v1/academics/offerings/{never_opened_offering_id}")
        assert offering_detail.json()["assigned_teacher_id"] is None

    async def test_offering_with_a_future_term_end_date_is_unassigned_even_if_closed_for_enrollment(
        self, app_client, admin_user, db_session
    ):
        """Real bug this replaces: enrollment is routinely closed well
        before a course actually finishes, so is_open_for_enrollment
        being False was never a safe signal that a course had ended.
        A future term_end_date means the course is still running."""
        teacher_id = await _create_teacher(app_client, db_session, code="TERM3C", cnic="76003-0000003-3")
        current_offering_id = await _create_course_offering(
            app_client, code="TERM3C", teacher_id=teacher_id,
            term_start_date="2026-01-01", term_end_date="2099-12-31",
        )
        # Enrollment closed (or never opened) mid-term — must not be
        # mistaken for "concluded".
        await app_client.patch(
            f"/api/v1/academics/offerings/{current_offering_id}", json={"is_open_for_enrollment": False}
        )

        status_resp = await app_client.post(
            f"/api/v1/employees/{teacher_id}/status", json={"new_status": "TERMINATED", "reason": "End of contract"}
        )
        assert status_resp.status_code == 200, status_resp.text

        offering_detail = await app_client.get(f"/api/v1/academics/offerings/{current_offering_id}")
        assert offering_detail.json()["assigned_teacher_id"] is None

    async def test_terminating_an_employee_with_no_offerings_is_unaffected(self, app_client, admin_user, db_session):
        teacher_id = await _create_teacher(app_client, db_session, code="TERM4", cnic="76004-0000001-1")
        status_resp = await app_client.post(
            f"/api/v1/employees/{teacher_id}/status", json={"new_status": "TERMINATED", "reason": "No offerings assigned"}
        )
        assert status_resp.status_code == 200, status_resp.text

    async def test_a_different_teachers_open_offering_is_unaffected(self, app_client, admin_user, db_session):
        """Confirms the fix is scoped to the terminated teacher's own
        offerings only — it must not touch anyone else's."""
        teacher_a = await _create_teacher(app_client, db_session, code="TERM5A", cnic="76005-0000001-1")
        teacher_b = await _create_teacher(app_client, db_session, code="TERM5B", cnic="76006-0000001-1")
        offering_b = await _create_course_offering(app_client, code="TERM5B", teacher_id=teacher_b)
        await app_client.patch(f"/api/v1/academics/offerings/{offering_b}", json={"is_open_for_enrollment": True})

        await app_client.post(
            f"/api/v1/employees/{teacher_a}/status", json={"new_status": "TERMINATED", "reason": "Unrelated termination"}
        )

        offering_detail = await app_client.get(f"/api/v1/academics/offerings/{offering_b}")
        assert offering_detail.json()["assigned_teacher_id"] == teacher_b


class TestJobRoleCannotGrantAdminOrStudent:
    """Real security gap: a job role used to accept ADMIN or STUDENT as
    its default_role_code — one misconfigured job title would have made
    every new hire an admin (or, just as wrong, a student)."""

    async def test_cannot_create_a_job_role_mapped_to_admin(self, app_client, admin_user):
        await _login_admin(app_client)
        resp = await app_client.post(
            "/api/v1/job-roles", json={"code": "SNEAKY", "title": "Sneaky Admin", "default_role_code": "ADMIN"}
        )
        assert resp.status_code == 422

    async def test_cannot_create_a_job_role_mapped_to_student(self, app_client, admin_user):
        await _login_admin(app_client)
        resp = await app_client.post(
            "/api/v1/job-roles", json={"code": "SNEAKY2", "title": "Sneaky Student", "default_role_code": "STUDENT"}
        )
        assert resp.status_code == 422

    async def test_a_real_staff_role_is_still_accepted(self, app_client, admin_user):
        await _login_admin(app_client)
        resp = await app_client.post(
            "/api/v1/job-roles", json={"code": "REALROLE", "title": "Real Role", "default_role_code": "TEACHER"}
        )
        assert resp.status_code == 201, resp.text

    async def test_cannot_update_an_existing_job_role_to_admin(self, app_client, admin_user, db_session):
        job_role = JobRole(code="EXISTING", title="Existing", default_role_code="TEACHER")
        db_session.add(job_role)
        await db_session.commit()
        await _login_admin(app_client)

        resp = await app_client.patch(f"/api/v1/job-roles/{job_role.id}", json={"default_role_code": "ADMIN"})
        assert resp.status_code == 422