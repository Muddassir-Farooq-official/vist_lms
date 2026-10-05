from __future__ import annotations

import pytest

from app.employees.models import JobRole

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


def _student_payload(**overrides):
    payload = {
        "first_name": "Aca", "last_name": "Demic",
        "date_of_birth": "2004-01-01", "gender": "Female",
        "cnic": "60001-0000001-1", "contact_no": "03006660000",
        "marital_status": "Single", "religion": "Islam", "nationality": "Pakistani",
        "guardian_name": "Guardian", "guardian_cnic": "60001-9999999-9",
        "relation_with_guardian": "Father", "emergency_contact_no": "03006669999",
        "present_address": "Somewhere", "province": "Punjab", "district": "Lahore", "city": "Lahore",
    }
    payload.update(overrides)
    return payload


def _employee_payload(job_role_id, **overrides):
    payload = {
        "first_name": "Teach", "last_name": "Er",
        "date_of_birth": "1985-01-01", "gender": "Male",
        "cnic": "50001-0000001-1", "contact_no": "03005550000",
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


async def _create_teacher(app_client, db_session, *, code, cnic) -> tuple[str, str]:
    job_role = JobRole(code=code, title="Teacher", default_role_code="TEACHER")
    db_session.add(job_role)
    await db_session.commit()
    await _login_admin(app_client)
    resp = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id, cnic=cnic))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["id"], body["username"]


async def _create_program(app_client, *, code, total_semesters=8, max_credit_hours=None) -> str:
    payload = {"code": code, "name": f"{code} Program", "duration_years": 4, "total_semesters": total_semesters}
    if max_credit_hours is not None:
        payload["max_credit_hours_per_semester"] = max_credit_hours
    resp = await app_client.post("/api/v1/programs", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _create_course(app_client, *, code, credit_hours=3) -> str:
    resp = await app_client.post("/api/v1/academics/courses", json={"code": code, "title": f"{code} Title", "credit_hours": credit_hours})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _link_course(app_client, course_id, program_id, semester_number):
    resp = await app_client.post(
        f"/api/v1/academics/courses/{course_id}/programs",
        json={"program_id": program_id, "semester_number": semester_number},
    )
    assert resp.status_code == 201, resp.text


async def _create_open_offering(app_client, *, course_id, term_label, assigned_teacher_id=None) -> str:
    resp = await app_client.post(
        "/api/v1/academics/offerings",
        json={"course_id": course_id, "term_label": term_label, "assigned_teacher_id": assigned_teacher_id},
    )
    assert resp.status_code == 201, resp.text
    offering_id = resp.json()["id"]
    open_resp = await app_client.patch(f"/api/v1/academics/offerings/{offering_id}", json={"is_open_for_enrollment": True})
    assert open_resp.status_code == 200, open_resp.text
    return offering_id


class TestEnrollmentEligibility:
    async def test_enroll_succeeds_when_course_matches_program_and_semester(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        program_id = await _create_program(app_client, code="ELIG1")
        course_id = await _create_course(app_client, code="ELIG1-C1")
        await _link_course(app_client, course_id, program_id, semester_number=1)
        offering_id = await _create_open_offering(app_client, course_id=course_id, term_label="Fall 2026")

        _, username = await _create_and_activate_student(app_client, cnic="61001-0000001-1", program_id=program_id, current_semester=1)
        await _login(app_client, username, "StudentPass123!")
        resp = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_id})
        assert resp.status_code == 201, resp.text

    async def test_enroll_rejected_when_course_not_in_program_curriculum(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        program_id = await _create_program(app_client, code="ELIG2")
        course_id = await _create_course(app_client, code="ELIG2-C1")
        # Deliberately never linked to the program.
        offering_id = await _create_open_offering(app_client, course_id=course_id, term_label="Fall 2026")

        _, username = await _create_and_activate_student(app_client, cnic="61002-0000001-1", program_id=program_id, current_semester=1)
        await _login(app_client, username, "StudentPass123!")
        resp = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_id})
        assert resp.status_code == 422
        assert "curriculum" in resp.json()["error"]["message"].lower()

    async def test_enroll_rejected_when_semester_mismatch(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        program_id = await _create_program(app_client, code="ELIG3")
        course_id = await _create_course(app_client, code="ELIG3-C1")
        await _link_course(app_client, course_id, program_id, semester_number=3)  # linked at semester 3
        offering_id = await _create_open_offering(app_client, course_id=course_id, term_label="Fall 2026")

        _, username = await _create_and_activate_student(app_client, cnic="61003-0000001-1", program_id=program_id, current_semester=1)  # student is at semester 1
        await _login(app_client, username, "StudentPass123!")
        resp = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_id})
        assert resp.status_code == 422
        assert "semester" in resp.json()["error"]["message"].lower()

    async def test_enroll_rejected_without_program_assigned(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        course_id = await _create_course(app_client, code="ELIG4-C1")
        offering_id = await _create_open_offering(app_client, course_id=course_id, term_label="Fall 2026")

        _, username = await _create_and_activate_student(app_client, cnic="61004-0000001-1")  # no program
        await _login(app_client, username, "StudentPass123!")
        resp = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_id})
        assert resp.status_code == 422
        assert "program" in resp.json()["error"]["message"].lower()

    async def test_cannot_enroll_twice_while_already_enrolled(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        program_id = await _create_program(app_client, code="ELIG5")
        course_id = await _create_course(app_client, code="ELIG5-C1")
        await _link_course(app_client, course_id, program_id, semester_number=1)
        offering_id = await _create_open_offering(app_client, course_id=course_id, term_label="Fall 2026")

        _, username = await _create_and_activate_student(app_client, cnic="61005-0000001-1", program_id=program_id, current_semester=1)
        await _login(app_client, username, "StudentPass123!")
        first = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_id})
        assert first.status_code == 201
        second = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_id})
        assert second.status_code == 422


class TestCreditHourCap:
    async def test_enrollment_rejected_once_it_would_exceed_the_programs_credit_hour_cap(
        self, app_client, admin_user, db_session
    ):
        await _login_admin(app_client)
        program_id = await _create_program(app_client, code="CREDIT1", max_credit_hours=10)
        course_a = await _create_course(app_client, code="CREDIT1-A", credit_hours=6)
        course_b = await _create_course(app_client, code="CREDIT1-B", credit_hours=6)  # 6+6=12 > cap of 10
        await _link_course(app_client, course_a, program_id, semester_number=1)
        await _link_course(app_client, course_b, program_id, semester_number=1)
        offering_a = await _create_open_offering(app_client, course_id=course_a, term_label="Fall 2026")
        offering_b = await _create_open_offering(app_client, course_id=course_b, term_label="Fall 2026")

        _, username = await _create_and_activate_student(app_client, cnic="62001-0000001-1", program_id=program_id, current_semester=1)
        await _login(app_client, username, "StudentPass123!")
        first = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_a})
        assert first.status_code == 201, first.text
        second = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_b})
        assert second.status_code == 422
        assert "credit" in second.json()["error"]["message"].lower()

    async def test_enrollment_succeeds_right_up_to_the_cap(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        program_id = await _create_program(app_client, code="CREDIT2", max_credit_hours=10)
        course_a = await _create_course(app_client, code="CREDIT2-A", credit_hours=6)
        course_b = await _create_course(app_client, code="CREDIT2-B", credit_hours=4)  # 6+4=10, exactly at cap
        await _link_course(app_client, course_a, program_id, semester_number=1)
        await _link_course(app_client, course_b, program_id, semester_number=1)
        offering_a = await _create_open_offering(app_client, course_id=course_a, term_label="Fall 2026")
        offering_b = await _create_open_offering(app_client, course_id=course_b, term_label="Fall 2026")

        _, username = await _create_and_activate_student(app_client, cnic="62002-0000001-1", program_id=program_id, current_semester=1)
        await _login(app_client, username, "StudentPass123!")
        first = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_a})
        assert first.status_code == 201, first.text
        second = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_b})
        assert second.status_code == 201, second.text


class TestRetakeHandling:
    async def test_retake_allowed_in_a_different_semester_after_completion(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        program_id = await _create_program(app_client, code="RETAKE1")
        course_id = await _create_course(app_client, code="RETAKE1-C1")
        await _link_course(app_client, course_id, program_id, semester_number=1)
        offering_1 = await _create_open_offering(app_client, course_id=course_id, term_label="Fall 2026")

        _student_id, username = await _create_and_activate_student(
            app_client, cnic="63001-0000001-1", program_id=program_id, current_semester=1
        )
        await _login(app_client, username, "StudentPass123!")
        enroll_resp = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_1})
        assert enroll_resp.status_code == 201, enroll_resp.text
        enrollment_id = enroll_resp.json()["id"]

        # Admin fails the student, then advances them to semester 2.
        await _login_admin(app_client)
        outcome_resp = await app_client.post(
            f"/api/v1/academics/enrollments/{enrollment_id}/outcome", json={"outcome": "FAILED"}
        )
        assert outcome_resp.status_code == 200, outcome_resp.text
        advance_resp = await app_client.post("/api/v1/students/advance-semester", json={"program_id": program_id})
        assert advance_resp.status_code == 200, advance_resp.text

        # A second offering of the SAME course, a later term — the
        # student is now at semester 2, but this course is curriculum-
        # linked at semester 1. A first attempt would be rejected (see
        # TestEnrollmentEligibility); a RETAKE must be allowed regardless.
        offering_2 = await _create_open_offering(app_client, course_id=course_id, term_label="Spring 2027")
        await _login(app_client, username, "StudentPass123!")
        retake_resp = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_2})
        assert retake_resp.status_code == 201, retake_resp.text
        assert retake_resp.json()["is_retake"] is True


class TestCourseFeeAutoChargeOnEnrollment:
    """Cross-module integration: EnrollmentService.enroll() calls
    FeeStructureService.assign_course_fee_for_enrollment() directly.
    Untested until now despite being a real, non-trivial trigger."""

    async def test_enrolling_in_a_course_with_an_active_course_scoped_fee_charges_it(
        self, app_client, admin_user, db_session
    ):
        await _login_admin(app_client)
        program_id = await _create_program(app_client, code="FEE1")
        course_id = await _create_course(app_client, code="FEE1-C1")
        await _link_course(app_client, course_id, program_id, semester_number=1)
        offering_id = await _create_open_offering(app_client, course_id=course_id, term_label="Fall 2026")

        type_resp = await app_client.post("/api/v1/fees/types", json={"code": "LABFEE", "name": "Lab Fee"})
        assert type_resp.status_code == 201, type_resp.text
        structure_resp = await app_client.post(
            "/api/v1/fees/structures",
            json={
                "fee_type_id": type_resp.json()["id"], "name": "Course Lab Fee", "term_label": "Fall 2026",
                "total_amount": 1500, "effective_from": "2026-09-01", "course_id": course_id,
            },
        )
        assert structure_resp.status_code == 201, structure_resp.text
        activate_resp = await app_client.post(f"/api/v1/fees/structures/{structure_resp.json()['id']}/activate")
        assert activate_resp.status_code == 200, activate_resp.text

        _, username = await _create_and_activate_student(app_client, cnic="64001-0000001-1", program_id=program_id, current_semester=1)
        await _login(app_client, username, "StudentPass123!")
        enroll_resp = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_id})
        assert enroll_resp.status_code == 201, enroll_resp.text

        my_fees = await app_client.get("/api/v1/fees/assignments/me")
        assert my_fees.status_code == 200
        matching = [a for a in my_fees.json() if a["total_charged"] == 1500]
        assert len(matching) == 1, my_fees.json()

    async def test_retake_enrollment_is_charged_the_course_fee_again(self, app_client, admin_user, db_session):
        """Approved decision, per app/fees/service.py's own comments:
        course-scoped fees are charged on EVERY enrollment attempt,
        including retakes — a second enrollment row for the same course
        must get its own, separate fee assignment."""
        await _login_admin(app_client)
        program_id = await _create_program(app_client, code="FEE2")
        course_id = await _create_course(app_client, code="FEE2-C1")
        await _link_course(app_client, course_id, program_id, semester_number=1)
        offering_1 = await _create_open_offering(app_client, course_id=course_id, term_label="Fall 2026")

        type_resp = await app_client.post("/api/v1/fees/types", json={"code": "LABFEE2", "name": "Lab Fee 2"})
        structure_resp = await app_client.post(
            "/api/v1/fees/structures",
            json={
                "fee_type_id": type_resp.json()["id"], "name": "Retake Lab Fee", "term_label": "Fall 2026",
                "total_amount": 800, "effective_from": "2026-09-01", "course_id": course_id,
            },
        )
        assert structure_resp.status_code == 201, structure_resp.text
        await app_client.post(f"/api/v1/fees/structures/{structure_resp.json()['id']}/activate")

        _student_id, username = await _create_and_activate_student(
            app_client, cnic="64002-0000001-1", program_id=program_id, current_semester=1
        )
        await _login(app_client, username, "StudentPass123!")
        enroll_resp = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_1})
        assert enroll_resp.status_code == 201
        enrollment_id = enroll_resp.json()["id"]

        await _login_admin(app_client)
        await app_client.post(f"/api/v1/academics/enrollments/{enrollment_id}/outcome", json={"outcome": "FAILED"})
        offering_2 = await _create_open_offering(app_client, course_id=course_id, term_label="Spring 2027")

        await _login(app_client, username, "StudentPass123!")
        retake_resp = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": offering_2})
        assert retake_resp.status_code == 201, retake_resp.text

        my_fees = await app_client.get("/api/v1/fees/assignments/me")
        matching = [a for a in my_fees.json() if a["total_charged"] == 800]
        assert len(matching) == 2, my_fees.json()  # charged once per enrollment attempt


class TestAdvanceSemesterBulk:
    async def test_advances_eligible_students_and_skips_others_correctly(self, app_client, admin_user, db_session):
        from datetime import date as _date

        from app.students.models import Student, StudentStatus

        await _login_admin(app_client)
        program_id = await _create_program(app_client, code="ADV1", total_semesters=2)

        def _bare_student(cnic, **kw):
            return Student(
                first_name="Adv", last_name="Test", date_of_birth=_date(2004, 1, 1), gender="Female",
                cnic=cnic, contact_no="03000000000", marital_status="Single", religion="Islam",
                nationality="Pakistani", status=StudentStatus.ACTIVE,
                roll_number=f"ADV-{cnic[:5]}", **kw,
            )

        # Eligible: has a program and a semester below the max.
        eligible = _bare_student("65001-0000001-1", program_id=program_id, current_semester=1)
        # Already at the program's max semester — must be skipped.
        maxed = _bare_student("65002-0000001-1", program_id=program_id, current_semester=2)
        # No current_semester set at all — must be skipped.
        no_sem = _bare_student("65003-0000001-1", program_id=program_id)
        # No program at all — must be skipped (and excluded by the program_id filter too).
        no_prog = _bare_student("65004-0000001-1", current_semester=1)
        db_session.add_all([eligible, maxed, no_sem, no_prog])
        await db_session.commit()

        resp = await app_client.post("/api/v1/students/advance-semester", json={"program_id": program_id})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["advanced_count"] == 1
        assert body["skipped_already_at_max"] == 1
        assert body["skipped_no_semester_set"] == 1
        detail = await app_client.get(f"/api/v1/students/{eligible.id}")
        assert detail.status_code == 200
        assert detail.json()["current_semester"] == 2

    async def test_advancing_into_a_semester_with_an_active_fee_structure_charges_it(
        self, app_client, admin_user, db_session
    ):
        """The specific gap this function's own docstring says it
        closes: a fee structure scoped to semester 2 should charge a
        student the moment they're advanced INTO semester 2, not only
        students who were already there when the structure activated."""
        await _login_admin(app_client)
        program_id = await _create_program(app_client, code="ADV2", total_semesters=4)
        student_id, _username = await _create_and_activate_student(
            app_client, cnic="65005-0000001-1", program_id=program_id, current_semester=1
        )

        await _login_admin(app_client)
        type_resp = await app_client.post("/api/v1/fees/types", json={"code": "SEM2FEE", "name": "Semester 2 Fee"})
        structure_resp = await app_client.post(
            "/api/v1/fees/structures",
            json={
                "fee_type_id": type_resp.json()["id"], "name": "Semester 2 Only", "term_label": "Fall 2026",
                "total_amount": 2000, "effective_from": "2026-09-01",
                "program_ids": [program_id], "semester_numbers": [2],
            },
        )
        assert structure_resp.status_code == 201, structure_resp.text
        await app_client.post(f"/api/v1/fees/structures/{structure_resp.json()['id']}/activate")

        # At semester 1, the semester-2-scoped structure should NOT have charged this student yet.
        pre_assignments = await app_client.get("/api/v1/fees/assignments")
        pre_matching = [a for a in pre_assignments.json() if a["payer_id"] == student_id and a["total_charged"] == 2000]
        assert len(pre_matching) == 0

        advance_resp = await app_client.post("/api/v1/students/advance-semester", json={"program_id": program_id})
        assert advance_resp.status_code == 200, advance_resp.text

        post_assignments = await app_client.get("/api/v1/fees/assignments")
        post_matching = [a for a in post_assignments.json() if a["payer_id"] == student_id and a["total_charged"] == 2000]
        assert len(post_matching) == 1, post_assignments.json()


class TestTeacherAssignmentValidation:
    async def test_assigning_a_non_teacher_employee_to_an_offering_is_rejected(self, app_client, admin_user, db_session):
        job_role = JobRole(code="LIBSTAFF1", title="Librarian", default_role_code="LIBRARIAN")
        db_session.add(job_role)
        await db_session.commit()
        await _login_admin(app_client)
        emp_resp = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id, cnic="51001-0000001-1"))
        assert emp_resp.status_code == 201, emp_resp.text
        librarian_id = emp_resp.json()["id"]

        course_id = await _create_course(app_client, code="TCHVAL1")
        resp = await app_client.post(
            "/api/v1/academics/offerings",
            json={"course_id": course_id, "term_label": "Fall 2026", "assigned_teacher_id": librarian_id},
        )
        assert resp.status_code == 422
        assert "teacher" in resp.json()["error"]["message"].lower()

    async def test_assigning_a_real_teacher_succeeds(self, app_client, admin_user, db_session):
        teacher_id, _ = await _create_teacher(app_client, db_session, code="TCHVAL2", cnic="51002-0000001-1")
        course_id = await _create_course(app_client, code="TCHVAL2C")
        resp = await app_client.post(
            "/api/v1/academics/offerings",
            json={"course_id": course_id, "term_label": "Fall 2026", "assigned_teacher_id": teacher_id},
        )
        assert resp.status_code == 201, resp.text