from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.employees.models import JobRole

_DAY_NAMES = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
_TERM_START = date(2026, 1, 5)
_TERM_END = _TERM_START + timedelta(weeks=2)
_SLOT_DAY_OF_WEEK = _DAY_NAMES[_TERM_START.weekday()]

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


def _student_payload(**overrides):
    payload = {
        "first_name": "File", "last_name": "Access",
        "date_of_birth": "2004-01-01", "gender": "Female",
        "cnic": "70001-0000001-1", "contact_no": "03007770000",
        "marital_status": "Single", "religion": "Islam", "nationality": "Pakistani",
        "guardian_name": "Guardian", "guardian_cnic": "70001-9999999-9",
        "relation_with_guardian": "Father", "emergency_contact_no": "03007779999",
        "present_address": "Somewhere", "province": "Punjab", "district": "Lahore", "city": "Lahore",
    }
    payload.update(overrides)
    return payload


def _employee_payload(job_role_id, **overrides):
    payload = {
        "first_name": "Teach", "last_name": "Er",
        "date_of_birth": "1985-01-01", "gender": "Male",
        "cnic": "80001-0000001-1", "contact_no": "03008880000",
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


async def _create_and_activate_student(app_client, *, cnic, **student_overrides) -> tuple[str, str]:
    await _login_admin(app_client)
    create_resp = await app_client.post("/api/v1/students", json=_student_payload(cnic=cnic, **student_overrides))
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
    employee_id, username = body["id"], body["username"]
    await _login(app_client, username, "TeacherPass123!")
    change_resp = await app_client.post(
        "/api/v1/auth/change-password", json={"current_password": "TeacherPass123!", "new_password": "TeacherReal123!"}
    )
    assert change_resp.status_code == 204
    await _login(app_client, username, "TeacherReal123!")
    return employee_id, username


# ---------------------------------------------------------------------------
# Students: /me/* (safe by construction) and /{student_id}/* (RBAC +
# assert_can_view_student resource-scope check)
# ---------------------------------------------------------------------------
class TestStudentFileDownloadAuthorization:
    async def test_student_can_download_own_photo_via_me_endpoint(self, app_client, admin_user, db_session):
        _student_id, _username = await _create_and_activate_student(app_client, cnic="91001-0000001-1")
        photo_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 32
        upload = await app_client.post(
            "/api/v1/students/me/photo", files={"file": ("me.jpg", photo_bytes, "image/jpeg")}
        )
        assert upload.status_code == 200, upload.text
        download = await app_client.get("/api/v1/students/me/photo")
        assert download.status_code == 200
        assert download.content == photo_bytes

    async def test_student_cannot_download_another_students_photo_by_id(self, app_client, admin_user, db_session):
        victim_id, _ = await _create_and_activate_student(app_client, cnic="91002-0000001-1")
        await _login_admin(app_client)
        photo_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 32
        upload = await app_client.post(
            f"/api/v1/students/{victim_id}/photo", files={"file": ("victim.jpg", photo_bytes, "image/jpeg")}
        )
        assert upload.status_code == 200, upload.text

        await _create_and_activate_student(app_client, cnic="91003-0000001-1")  # attacker, now logged in
        resp = await app_client.get(f"/api/v1/students/{victim_id}/photo")
        assert resp.status_code == 403

    async def test_accounts_officer_and_admin_can_view_any_students_photo(self, app_client, admin_user, db_session):
        student_id, _ = await _create_and_activate_student(app_client, cnic="91004-0000001-1")
        await _login_admin(app_client)
        photo_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 32
        upload = await app_client.post(
            f"/api/v1/students/{student_id}/photo", files={"file": ("s.jpg", photo_bytes, "image/jpeg")}
        )
        assert upload.status_code == 200
        resp = await app_client.get(f"/api/v1/students/{student_id}/photo")
        assert resp.status_code == 200
        assert resp.content == photo_bytes


# ---------------------------------------------------------------------------
# Employees: /{employee_id}/* is gated on EMPLOYEE_VIEW, which the seed
# table grants to ADMIN only — confirming that here locks in the
# assumption the rest of this file's safety depends on.
# ---------------------------------------------------------------------------
class TestEmployeeFileDownloadAuthorization:
    async def test_non_admin_employee_cannot_download_another_employees_photo(
        self, app_client, admin_user, db_session
    ):
        target_id, _ = await _create_teacher(app_client, db_session, code="T-TARGET", cnic="82001-0000001-1")
        await _login_admin(app_client)
        photo_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 32
        upload = await app_client.post(
            f"/api/v1/employees/{target_id}/photo", files={"file": ("t.jpg", photo_bytes, "image/jpeg")}
        )
        assert upload.status_code == 200, upload.text

        await _create_teacher(app_client, db_session, code="T-ATTACKER", cnic="82002-0000001-1")
        resp = await app_client.get(f"/api/v1/employees/{target_id}/photo")
        assert resp.status_code == 403

    async def test_employee_can_download_own_photo_via_me_endpoint(self, app_client, admin_user, db_session):
        _, _username = await _create_teacher(app_client, db_session, code="T-SELF", cnic="82003-0000001-1")
        photo_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 32
        upload = await app_client.post(
            "/api/v1/employees/me/photo", files={"file": ("me.jpg", photo_bytes, "image/jpeg")}
        )
        assert upload.status_code == 200, upload.text
        resp = await app_client.get("/api/v1/employees/me/photo")
        assert resp.status_code == 200
        assert resp.content == photo_bytes


# ---------------------------------------------------------------------------
# Academics: syllabus + lecture resources are gated by
# _can_view_offering_content (staff / assigned teacher / enrolled student)
# ---------------------------------------------------------------------------
class TestAcademicsFileDownloadAuthorization:
    async def _build_offering_with_content(self, app_client, db_session, *, teacher_code, teacher_cnic):
        """Admin creates a course + offering (with term dates set),
        assigns a teacher, opens enrollment, and uploads a syllabus +
        creates a timetable slot (generating one class session) with
        one resource on that session. Returns (offering_id,
        syllabus_bytes, resource_id, resource_bytes, teacher_id,
        teacher_username)."""
        teacher_id, teacher_username = await _create_teacher(
            app_client, db_session, code=teacher_code, cnic=teacher_cnic
        )
        await _login_admin(app_client)
        course_resp = await app_client.post(
            "/api/v1/academics/courses", json={"code": f"CRS-{teacher_code}", "title": "Anatomy I", "credit_hours": 3}
        )
        assert course_resp.status_code == 201, course_resp.text
        course_id = course_resp.json()["id"]

        offering_resp = await app_client.post(
            "/api/v1/academics/offerings",
            json={
                "course_id": course_id, "term_label": "Fall 2026", "assigned_teacher_id": teacher_id,
                "term_start_date": str(_TERM_START), "term_end_date": str(_TERM_END),
            },
        )
        assert offering_resp.status_code == 201, offering_resp.text
        offering_id = offering_resp.json()["id"]

        open_resp = await app_client.patch(
            f"/api/v1/academics/offerings/{offering_id}", json={"is_open_for_enrollment": True}
        )
        assert open_resp.status_code == 200, open_resp.text

        syllabus_bytes = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"\x00" * 32
        syllabus_upload = await app_client.post(
            f"/api/v1/academics/offerings/{offering_id}/syllabus",
            files={"file": ("syllabus.pdf", syllabus_bytes, "application/pdf")},
        )
        assert syllabus_upload.status_code == 200, syllabus_upload.text

        period_resp = await app_client.post(
            "/api/v1/academics/periods", json={"period_number": 1, "start_time": "09:00:00", "end_time": "10:00:00"}
        )
        assert period_resp.status_code == 201, period_resp.text
        period_id = period_resp.json()["id"]

        slot_resp = await app_client.post(
            f"/api/v1/academics/offerings/{offering_id}/timetable-slots",
            json={"day_of_week": _SLOT_DAY_OF_WEEK, "period_id": period_id, "room": "Room 1"},
        )
        assert slot_resp.status_code == 201, slot_resp.text
        sessions_resp = await app_client.get(f"/api/v1/academics/offerings/{offering_id}/class-sessions")
        assert sessions_resp.status_code == 200, sessions_resp.text
        class_session_id = sessions_resp.json()[0]["id"]

        resource_bytes = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"\x11" * 32
        resource_resp = await app_client.post(
            f"/api/v1/academics/class-sessions/{class_session_id}/resources?resource_type=SLIDES",
            files={"file": ("slides.pdf", resource_bytes, "application/pdf")},
        )
        assert resource_resp.status_code == 201, resource_resp.text
        resource_id = resource_resp.json()["id"]

        return offering_id, syllabus_bytes, resource_id, resource_bytes, teacher_id, teacher_username

    async def test_enrolled_student_can_download_syllabus_and_lecture_resource(
        self, app_client, admin_user, db_session
    ):
        offering_id, syllabus_bytes, resource_id, resource_bytes, _, _ = await self._build_offering_with_content(
            app_client, db_session, teacher_code="T-ENR", teacher_cnic="83001-0000001-1"
        )
        program_resp = await app_client.post(
            "/api/v1/programs", json={"code": "ENRTEST", "name": "Enrollment Test Program", "duration_years": 4, "total_semesters": 8}
        )
        assert program_resp.status_code == 201, program_resp.text
        program_id = program_resp.json()["id"]

        # The course must be part of the student's program curriculum at
        # the student's current semester — a real eligibility rule
        # (app/academics/service.py::enroll), not something to route
        # around; set both sides up to satisfy it honestly.
        offering_detail = await app_client.get(f"/api/v1/academics/offerings/{offering_id}")
        assert offering_detail.status_code == 200, offering_detail.text
        course_id = offering_detail.json()["course_id"]
        link_resp = await app_client.post(
            f"/api/v1/academics/courses/{course_id}/programs",
            json={"program_id": program_id, "semester_number": 1},
        )
        assert link_resp.status_code == 201, link_resp.text

        _, student_username = await _create_and_activate_student(
            app_client, cnic="93001-0000001-1", program_id=program_id, current_semester=1
        )

        await _login(app_client, student_username, "StudentPass123!")
        enroll_resp = await app_client.post(
            "/api/v1/academics/enrollments/me", json={"course_offering_id": offering_id}
        )
        assert enroll_resp.status_code == 201, enroll_resp.text

        syllabus_resp = await app_client.get(f"/api/v1/academics/offerings/{offering_id}/syllabus")
        assert syllabus_resp.status_code == 200
        assert syllabus_resp.content == syllabus_bytes

        resource_resp = await app_client.get(f"/api/v1/academics/class-session-resources/{resource_id}")
        assert resource_resp.status_code == 200
        assert resource_resp.content == resource_bytes

    async def test_unenrolled_student_cannot_download_syllabus_or_lecture_resource(
        self, app_client, admin_user, db_session
    ):
        offering_id, _, resource_id, _, _, _ = await self._build_offering_with_content(
            app_client, db_session, teacher_code="T-UNENR", teacher_cnic="83002-0000001-1"
        )
        # A student who exists but never enrolled in THIS offering.
        await _create_and_activate_student(app_client, cnic="93002-0000001-1")

        syllabus_resp = await app_client.get(f"/api/v1/academics/offerings/{offering_id}/syllabus")
        assert syllabus_resp.status_code == 403

        resource_resp = await app_client.get(f"/api/v1/academics/class-session-resources/{resource_id}")
        assert resource_resp.status_code == 403

    async def test_assigned_teacher_can_download_syllabus_and_lecture_resource(
        self, app_client, admin_user, db_session
    ):
        offering_id, syllabus_bytes, resource_id, resource_bytes, _teacher_id, teacher_username = (
            await self._build_offering_with_content(
                app_client, db_session, teacher_code="T-ASSIGNED", teacher_cnic="83003-0000001-1"
            )
        )
        await _login(app_client, teacher_username, "TeacherReal123!")

        syllabus_resp = await app_client.get(f"/api/v1/academics/offerings/{offering_id}/syllabus")
        assert syllabus_resp.status_code == 200
        assert syllabus_resp.content == syllabus_bytes

        resource_resp = await app_client.get(f"/api/v1/academics/class-session-resources/{resource_id}")
        assert resource_resp.status_code == 200
        assert resource_resp.content == resource_bytes

    async def test_unrelated_teacher_cannot_download_someone_elses_offering_content(
        self, app_client, admin_user, db_session
    ):
        offering_id, _, resource_id, _, _, _ = await self._build_offering_with_content(
            app_client, db_session, teacher_code="T-OWNER", teacher_cnic="83004-0000001-1"
        )
        # A second teacher, not assigned to this offering at all.
        _, other_teacher_username = await _create_teacher(
            app_client, db_session, code="T-UNRELATED", cnic="83005-0000001-1"
        )
        await _login(app_client, other_teacher_username, "TeacherReal123!")

        syllabus_resp = await app_client.get(f"/api/v1/academics/offerings/{offering_id}/syllabus")
        assert syllabus_resp.status_code == 403

        resource_resp = await app_client.get(f"/api/v1/academics/class-session-resources/{resource_id}")
        assert resource_resp.status_code == 403