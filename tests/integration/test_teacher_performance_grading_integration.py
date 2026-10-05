from __future__ import annotations

import pytest

from app.employees.models import JobRole

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


def _student_payload(**overrides):
    payload = {
        "first_name": "Perf", "last_name": "Test",
        "date_of_birth": "2004-01-01", "gender": "Female",
        "cnic": "30001-0000001-1", "contact_no": "03003000000",
        "marital_status": "Single", "religion": "Islam", "nationality": "Pakistani",
        "guardian_name": "Guardian", "guardian_cnic": "30001-9999999-9",
        "relation_with_guardian": "Father", "emergency_contact_no": "03003009999",
        "present_address": "Somewhere", "province": "Punjab", "district": "Lahore", "city": "Lahore",
    }
    payload.update(overrides)
    return payload


def _employee_payload(job_role_id, **overrides):
    payload = {
        "first_name": "Perf", "last_name": "Teacher",
        "date_of_birth": "1985-01-01", "gender": "Male",
        "cnic": "31001-0000001-1", "contact_no": "03003100000",
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


async def _create_and_activate_student(app_client, *, cnic, **overrides) -> tuple[str, str]:
    await _login_admin(app_client)
    create_resp = await app_client.post("/api/v1/students", json=_student_payload(cnic=cnic, **overrides))
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    student_id, username = body["id"], body["username"]
    await _login(app_client, username, "vertex123")
    change_resp = await app_client.post(
        "/api/v1/auth/change-password", json={"current_password": "vertex123", "new_password": "StudentPass123!"}
    )
    assert change_resp.status_code == 204
    await _login(app_client, username, "StudentPass123!")
    return student_id, username


async def _create_teacher(app_client, db_session, *, code, cnic) -> str:
    job_role = JobRole(code=code, title="Teacher", default_role_code="TEACHER")
    db_session.add(job_role)
    await db_session.commit()
    await _login_admin(app_client)
    resp = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id, cnic=cnic))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _publish_results(client, offering_id):
    """The marks are a draft until submitted, approved and published; only then is pass/fail decided."""
    for step in (f"/api/v1/results/offerings/{offering_id}/submit", f"/api/v1/results/sheets/{offering_id}/approve", f"/api/v1/results/sheets/{offering_id}/publish"):
        r = await client.post(step)
        assert r.status_code == 200, (step, r.text)


class TestTeacherPerformanceReflectsAutoComputedGrades:
    async def test_pass_rate_correctly_counts_auto_computed_passed_and_failed_outcomes(
        self, app_client, admin_user, db_session
    ):
        """Real grading flow end to end, not EnrollmentService.set_outcome
        — this endpoint was written before GradingService existed and
        has zero test coverage; confirm it actually reflects the new
        auto-compute path, not just something that happens to share a
        field name."""
        teacher_id = await _create_teacher(app_client, db_session, code="PERF1", cnic="32001-0000001-1")

        program_resp = await app_client.post(
            "/api/v1/programs", json={"code": "PERF1", "name": "Perf Program", "duration_years": 4, "total_semesters": 8}
        )
        program_id = program_resp.json()["id"]
        course_resp = await app_client.post(
            "/api/v1/academics/courses", json={"code": "PERF1-C1", "title": "Test Course", "credit_hours": 3}
        )
        course_id = course_resp.json()["id"]
        await app_client.post(
            f"/api/v1/academics/courses/{course_id}/programs", json={"program_id": program_id, "semester_number": 1}
        )
        offering_resp = await app_client.post(
            "/api/v1/academics/offerings",
            json={"course_id": course_id, "term_label": "Fall 2026", "assigned_teacher_id": teacher_id},
        )
        offering_id = offering_resp.json()["id"]
        await app_client.patch(f"/api/v1/academics/offerings/{offering_id}", json={"is_open_for_enrollment": True})

        type_resp = await app_client.post(
            "/api/v1/academics/assessment-types", json={"name": "PERF1 Final", "max_marks": 100}
        )
        type_id = type_resp.json()["id"]
        component_resp = await app_client.post(
            f"/api/v1/academics/offerings/{offering_id}/assessments", json={"assessment_type_id": type_id}
        )
        component_id = component_resp.json()["id"]

        # Student A: enrolls, scores 80 -> auto PASSED (>= 50% default cutoff).
        _student_a_id, username_a = await _create_and_activate_student(
            app_client, cnic="33001-0000001-1", program_id=program_id, current_semester=1
        )
        await _login(app_client, username_a, "StudentPass123!")
        enroll_a = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_id})
        enrollment_a_id = enroll_a.json()["id"]

        # Student B: enrolls, scores 30 -> auto FAILED.
        _student_b_id, username_b = await _create_and_activate_student(
            app_client, cnic="33002-0000001-1", program_id=program_id, current_semester=1
        )
        await _login(app_client, username_b, "StudentPass123!")
        enroll_b = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_id})
        enrollment_b_id = enroll_b.json()["id"]

        # Student C: enrolls and was absent for the exam -> the teacher records 0, so C is FAILED (a sheet cannot be submitted with a student's marks missing).
        _student_c_id, username_c = await _create_and_activate_student(
            app_client, cnic="33003-0000001-1", program_id=program_id, current_semester=1
        )
        await _login(app_client, username_c, "StudentPass123!")
        enroll_c = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_id})
        enrollment_c_id = enroll_c.json()["id"]

        await _login_admin(app_client)
        await app_client.post(f"/api/v1/academics/enrollments/{enrollment_a_id}/marks/{component_id}", json={"obtained_marks": 80})
        await app_client.post(f"/api/v1/academics/enrollments/{enrollment_b_id}/marks/{component_id}", json={"obtained_marks": 30})

        # Missing marks stop the sheet being submitted.
        blocked = await app_client.post(f"/api/v1/results/offerings/{offering_id}/submit")
        assert blocked.status_code == 422 and "missing for 1 student" in blocked.text
        await app_client.post(f"/api/v1/academics/enrollments/{enrollment_c_id}/marks/{component_id}", json={"obtained_marks": 0})

        # Until the results are published nothing is decided, so the teacher's pass rate has nothing to count yet.
        before = next(e for e in (await app_client.get("/api/v1/academics/teacher-performance")).json() if e["teacher_id"] == teacher_id)
        assert (before["passed_count"], before["failed_count"], before["pass_rate"]) == (0, 0, None)

        await _publish_results(app_client, offering_id)

        # Sanity: outcomes really were set by the auto-compute path.
        roster = (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/roster")).json()
        outcome_a = next(e for e in roster if e["id"] == enrollment_a_id)["outcome"]
        outcome_b = next(e for e in roster if e["id"] == enrollment_b_id)["outcome"]
        assert outcome_a == "PASSED"
        assert outcome_b == "FAILED"

        perf_resp = await app_client.get("/api/v1/academics/teacher-performance")
        assert perf_resp.status_code == 200, perf_resp.text
        entry = next(e for e in perf_resp.json() if e["teacher_id"] == teacher_id)
        assert entry["offerings_count"] == 1
        assert entry["total_enrollments"] == 3  # A, B and C all enrolled
        assert entry["passed_count"] == 1
        assert entry["failed_count"] == 2
        assert abs(entry["pass_rate"] - 1 / 3) < 0.01  # 1 passed / (1 passed + 2 failed)

    async def test_teacher_with_no_graded_enrollments_has_null_pass_rate(self, app_client, admin_user, db_session):
        teacher_id = await _create_teacher(app_client, db_session, code="PERF2", cnic="32002-0000001-1")
        course_resp = await app_client.post(
            "/api/v1/academics/courses", json={"code": "PERF2-C1", "title": "Ungraded Course", "credit_hours": 3}
        )
        course_id = course_resp.json()["id"]
        await app_client.post(
            "/api/v1/academics/offerings",
            json={"course_id": course_id, "term_label": "Fall 2026", "assigned_teacher_id": teacher_id},
        )

        perf_resp = await app_client.get("/api/v1/academics/teacher-performance")
        entry = next(e for e in perf_resp.json() if e["teacher_id"] == teacher_id)
        assert entry["pass_rate"] is None
        assert entry["passed_count"] == 0
        assert entry["failed_count"] == 0