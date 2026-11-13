"""Student directory and record screens: readable list columns, the program filter, and the read-only lookup for the exam office."""
from __future__ import annotations

from app.employees.models import JobRole
from tests.integration.test_file_download_authorization import _create_and_activate_student, _employee_payload, _login, _login_admin


async def test_directory_rows_are_readable_but_hold_no_cnic_guardian_or_address(app_client, admin_user, db_session):
    await _create_and_activate_student(app_client, cnic="94001-0000001-1")
    await _login_admin(app_client)
    row = (await app_client.get("/api/v1/students")).json()["items"][0]
    for key in ("program_code", "program_name", "gender", "contact_no", "admission_session", "has_photo", "current_semester", "section_label"):
        assert key in row
    assert not {"cnic", "guardian_cnic", "guardian_contact_no", "present_address"} & set(row)
    # The one deliberate exception (owner decision, Nov 2026): staff who handle fees see the father's / guardian's
    # NAME (and relation) to tell students apart; never the CNIC or phone. Others get none (test_accounts_father_lookup).
    assert row["guardian_name"] == "Guardian" and row["guardian_label"] == "d/o Guardian"     # the administrator handles fees
    assert (await app_client.get("/api/v1/students/programs")).status_code == 200

    page = (await app_client.get("/dashboard/admin/students")).text
    assert 'id="student-sheet"' in page and 'data-editable="true"' in page and "student-sheet.js" in page
    assert 'id="ss-photo-input"' in page and "Upload / Replace" not in page


async def test_exam_office_can_open_a_record_read_only(app_client, admin_user, db_session):
    sid, _ = await _create_and_activate_student(app_client, cnic="94002-0000001-1")
    job = JobRole(code="EXAM_T", title="Exam Controller", default_role_code="EXAM_CONTROLLER")
    db_session.add(job)
    await db_session.commit()
    await _login_admin(app_client)
    made = await app_client.post("/api/v1/employees", json=_employee_payload(job.id, cnic="84002-0000001-1"))
    assert made.status_code == 201, made.text
    username = made.json()["username"]
    await _login(app_client, username, "TeacherPass123!")
    assert (await app_client.post("/api/v1/auth/change-password", json={"current_password": "TeacherPass123!", "new_password": "ExamReal123!x"})).status_code == 204
    await _login(app_client, username, "ExamReal123!x")

    assert (await app_client.get("/api/v1/students/programs")).status_code == 200
    assert (await app_client.get(f"/api/v1/students/{sid}/full-profile")).status_code == 200
    assert (await app_client.patch(f"/api/v1/students/{sid}", json={"section_label": "Z"})).status_code == 403
    page = (await app_client.get("/dashboard/exam/students")).text
    assert 'data-editable="false"' in page and 'id="ss-photo-input"' not in page and 'id="edit-student-form"' not in page


async def test_staff_directory_rows_show_the_job_title_but_no_cnic_or_bank(app_client, admin_user, db_session):
    job = JobRole(code="SCR_T", title="Driver", default_role_code="STAFF")
    db_session.add(job)
    await db_session.commit()
    await _login_admin(app_client)
    assert (await app_client.post("/api/v1/employees", json=_employee_payload(job.id, cnic="84003-0000001-1"))).status_code == 201
    row = next(r for r in (await app_client.get("/api/v1/employees")).json()["items"] if r["job_role_id"] == str(job.id))
    assert row["job_title"] == "Driver" and "contact_no" in row and "has_photo" in row
    assert not {"cnic", "bank_iban", "address_line"} & set(row)
    page = (await app_client.get("/dashboard/admin/employees")).text
    assert 'id="employee-sheet"' in page and 'id="es-photo-input"' in page and "Upload / Replace" not in page
    assert 'value="STAFF"' in page and 'value="ADMIN"' not in page.split('id="jr-default-role"')[1].split("</select>")[0]


async def test_programs_overview_counts_students_and_curriculum(app_client, admin_user, db_session):
    await _login_admin(app_client)
    made = await app_client.post("/api/v1/programs", json={"code": "PGX", "name": "Program X", "duration_years": 2, "total_semesters": 4})
    assert made.status_code == 201, made.text
    rows = (await app_client.get("/api/v1/programs/overview")).json()
    assert rows and all({"students", "curriculum", "code", "total_semesters"} <= set(r) for r in rows)
    assert all({"courses", "credit_hours", "semesters_planned"} <= set(r["curriculum"]) for r in rows)
    page = (await app_client.get("/dashboard/admin/programs")).text
    assert 'id="programs-grid"' in page and 'id="p-maxch"' in page and 'id="ep-maxch"' in page