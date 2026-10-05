from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


def _student_payload(**overrides):
    payload = {
        "first_name": "Life", "last_name": "Cycle",
        "date_of_birth": "2004-01-01", "gender": "Female",
        "cnic": "70101-0000001-1", "contact_no": "03007770000",
        "marital_status": "Single", "religion": "Islam", "nationality": "Pakistani",
        "guardian_name": "Guardian", "guardian_cnic": "70101-9999999-9",
        "relation_with_guardian": "Father", "emergency_contact_no": "03007779999",
        "present_address": "Somewhere", "province": "Punjab", "district": "Lahore", "city": "Lahore",
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


async def _create_program_course_offering(app_client, *, code) -> tuple[str, str, str]:
    program_resp = await app_client.post(
        "/api/v1/programs", json={"code": code, "name": f"{code} Program", "duration_years": 4, "total_semesters": 8}
    )
    assert program_resp.status_code == 201, program_resp.text
    program_id = program_resp.json()["id"]

    course_resp = await app_client.post(
        "/api/v1/academics/courses", json={"code": f"{code}-C1", "title": "Test Course", "credit_hours": 3}
    )
    assert course_resp.status_code == 201, course_resp.text
    course_id = course_resp.json()["id"]

    link_resp = await app_client.post(
        f"/api/v1/academics/courses/{course_id}/programs", json={"program_id": program_id, "semester_number": 1}
    )
    assert link_resp.status_code == 201, link_resp.text

    offering_resp = await app_client.post(
        "/api/v1/academics/offerings", json={"course_id": course_id, "term_label": "Fall 2026"}
    )
    assert offering_resp.status_code == 201, offering_resp.text
    offering_id = offering_resp.json()["id"]
    open_resp = await app_client.patch(f"/api/v1/academics/offerings/{offering_id}", json={"is_open_for_enrollment": True})
    assert open_resp.status_code == 200, open_resp.text

    return program_id, course_id, offering_id


class TestGraduationBlockedByOpenCoursework:
    async def test_cannot_graduate_with_an_enrolled_course(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        program_id, _, offering_id = await _create_program_course_offering(app_client, code="GRAD1")
        student_id, username = await _create_and_activate_student(
            app_client, cnic="71001-0000001-1", program_id=program_id, current_semester=1
        )

        await _login(app_client, username, "StudentPass123!")
        enroll_resp = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_id})
        assert enroll_resp.status_code == 201, enroll_resp.text

        await _login_admin(app_client)
        status_resp = await app_client.post(
            f"/api/v1/students/{student_id}/status", json={"new_status": "GRADUATED", "reason": "End of program"}
        )
        assert status_resp.status_code == 422
        assert "enrollment" in status_resp.json()["error"]["message"].lower()

        # The status must genuinely be unchanged, not just the error shown.
        detail = await app_client.get(f"/api/v1/students/{student_id}")
        assert detail.json()["status"] == "ACTIVE"

    async def test_can_graduate_once_all_coursework_is_resolved(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        program_id, _, offering_id = await _create_program_course_offering(app_client, code="GRAD2")
        student_id, username = await _create_and_activate_student(
            app_client, cnic="71002-0000001-1", program_id=program_id, current_semester=1
        )

        await _login(app_client, username, "StudentPass123!")
        enroll_resp = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_id})
        enrollment_id = enroll_resp.json()["id"]

        await _login_admin(app_client)
        outcome_resp = await app_client.post(
            f"/api/v1/academics/enrollments/{enrollment_id}/outcome", json={"outcome": "PASSED"}
        )
        assert outcome_resp.status_code == 200, outcome_resp.text

        status_resp = await app_client.post(
            f"/api/v1/students/{student_id}/status", json={"new_status": "GRADUATED", "reason": "End of program"}
        )
        assert status_resp.status_code == 200, status_resp.text
        assert status_resp.json()["status"] == "GRADUATED"


class TestGraduationBlockedByOutstandingLibraryBooks:
    async def _issue_a_book(self, app_client, student_id):
        book_resp = await app_client.post(
            "/api/v1/library/books", json={"title": "Clearance Test Book", "author": "A. Uthor", "initial_copies": 1}
        )
        assert book_resp.status_code == 201, book_resp.text
        book_id = book_resp.json()["id"]
        copies_resp = await app_client.get(f"/api/v1/library/books/{book_id}/copies")
        copy_id = copies_resp.json()[0]["id"]
        issue_resp = await app_client.post(
            "/api/v1/library/issues",
            json={"book_copy_id": copy_id, "borrower_type": "STUDENT", "borrower_id": student_id},
        )
        assert issue_resp.status_code == 201, issue_resp.text
        return issue_resp.json()["id"]

    async def test_cannot_graduate_with_a_checked_out_book(self, app_client, admin_user, db_session):
        student_id, _ = await _create_and_activate_student(app_client, cnic="72001-0000001-1")
        await _login_admin(app_client)
        await self._issue_a_book(app_client, student_id)

        status_resp = await app_client.post(
            f"/api/v1/students/{student_id}/status", json={"new_status": "GRADUATED", "reason": "End of program"}
        )
        assert status_resp.status_code == 422
        assert "library" in status_resp.json()["error"]["message"].lower()

    async def test_can_graduate_once_the_book_is_returned(self, app_client, admin_user, db_session):
        student_id, _ = await _create_and_activate_student(app_client, cnic="72002-0000001-1")
        await _login_admin(app_client)
        issue_id = await self._issue_a_book(app_client, student_id)

        return_resp = await app_client.post(f"/api/v1/library/issues/{issue_id}/return", json={"is_lost": False})
        assert return_resp.status_code == 200, return_resp.text

        status_resp = await app_client.post(
            f"/api/v1/students/{student_id}/status", json={"new_status": "GRADUATED", "reason": "End of program"}
        )
        assert status_resp.status_code == 200, status_resp.text


class TestDroppingAutoWithdrawsEnrollments:
    async def test_dropping_a_student_auto_withdraws_their_active_enrollment(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        program_id, _, offering_id = await _create_program_course_offering(app_client, code="DROP1")
        student_id, username = await _create_and_activate_student(
            app_client, cnic="73001-0000001-1", program_id=program_id, current_semester=1
        )

        await _login(app_client, username, "StudentPass123!")
        enroll_resp = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_id})
        enrollment_id = enroll_resp.json()["id"]

        before = await app_client.get(f"/api/v1/academics/offerings/{offering_id}")
        assert before.json()["enrolled_count"] == 1

        await _login_admin(app_client)
        status_resp = await app_client.post(
            f"/api/v1/students/{student_id}/status", json={"new_status": "DROPPED", "reason": "Left the institute"}
        )
        assert status_resp.status_code == 200, status_resp.text
        assert status_resp.json()["status"] == "DROPPED"

        # The dropped student's row is no longer counted as a current enrollee...
        after = await app_client.get(f"/api/v1/academics/offerings/{offering_id}")
        assert after.json()["enrolled_count"] == 0

        # ...because the underlying enrollment was actually withdrawn, not just hidden.
        enrollments = await app_client.get(f"/api/v1/academics/offerings/{offering_id}/roster")
        assert enrollments.status_code == 200
        matching = [e for e in enrollments.json() if e["id"] == enrollment_id]
        assert len(matching) == 1
        assert matching[0]["status"] == "WITHDRAWN"

    async def test_dropping_a_student_with_no_enrollments_is_unaffected(self, app_client, admin_user, db_session):
        """The fix must not error out for the common case of a student
        with nothing to withdraw."""
        student_id, _ = await _create_and_activate_student(app_client, cnic="73002-0000001-1")
        await _login_admin(app_client)
        status_resp = await app_client.post(
            f"/api/v1/students/{student_id}/status", json={"new_status": "DROPPED", "reason": "Never enrolled anywhere"}
        )
        assert status_resp.status_code == 200, status_resp.text

    async def test_dropping_does_not_require_library_clearance(self, app_client, admin_user, db_session):
        """Deliberately different from GRADUATED — dropping out doesn't
        require returning books first (they may simply be gone); only
        graduation enforces clearance."""
        student_id, _ = await _create_and_activate_student(app_client, cnic="73003-0000001-1")
        await _login_admin(app_client)
        book_resp = await app_client.post(
            "/api/v1/library/books", json={"title": "Dropout Book", "author": "A. Uthor", "initial_copies": 1}
        )
        book_id = book_resp.json()["id"]
        copies_resp = await app_client.get(f"/api/v1/library/books/{book_id}/copies")
        copy_id = copies_resp.json()[0]["id"]
        await app_client.post(
            "/api/v1/library/issues",
            json={"book_copy_id": copy_id, "borrower_type": "STUDENT", "borrower_id": student_id},
        )

        status_resp = await app_client.post(
            f"/api/v1/students/{student_id}/status", json={"new_status": "DROPPED", "reason": "Left mid-term"}
        )
        assert status_resp.status_code == 200, status_resp.text