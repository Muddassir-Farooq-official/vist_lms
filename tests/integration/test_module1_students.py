"""Module 1 (Students) launch-readiness tests -- each locks in a gap that
was found during the pre-launch audit so it can't silently regress."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.fees.models import FeeAssignment
from app.imports.service import ImportService
from app.programs.models import Program
from app.students.models import Student

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


def _payload(**overrides):
    p = {
        "first_name": "Sana", "last_name": "Iqbal", "date_of_birth": "2005-03-03", "gender": "Female",
        "cnic": "35202-1111111-1", "contact_no": "03001110000", "marital_status": "Single",
        "religion": "Islam", "nationality": "Pakistani", "guardian_name": "Iqbal Ahmad",
        "guardian_cnic": "35202-2222222-2", "relation_with_guardian": "Father",
        "emergency_contact_no": "03001110001", "present_address": "12 Mall Road",
        "province": "Punjab", "district": "Lahore", "city": "Lahore",
    }
    p.update(overrides)
    return p


async def _program(db_session, code="BSN", semesters=8):
    program = Program(code=code, name=f"Program {code}", duration_years=4, total_semesters=semesters)
    db_session.add(program)
    await db_session.commit()
    return program


async def _login_admin(client):
    r = await client.post("/api/v1/auth/login", json=ADMIN)
    assert r.status_code == 200, r.text


async def _active_structure(client, program_id=None, semester=None):
    ft = await client.post("/api/v1/fees/types", json={"code": "TUI", "name": "Tuition"})
    body = {
        "fee_type_id": ft.json()["id"], "name": "Tuition Fall 2026", "term_label": "Fall 2026",
        "total_amount": 12000, "installment_count": 2, "effective_from": "2026-09-01",
    }
    if program_id:
        body["program_ids"] = [program_id]
    if semester:
        body["semester_numbers"] = [semester]
    r = await client.post("/api/v1/fees/structures", json=body)
    assert r.status_code == 201, r.text
    a = await client.post(f"/api/v1/fees/structures/{r.json()['id']}/activate")
    assert a.status_code == 200, a.text
    return r.json()["id"]


async def test_new_attributes_saved_and_returned(app_client, admin_user, db_session):
    await _login_admin(app_client)
    resp = await app_client.post(
        "/api/v1/students",
        json=_payload(email="Sana@Example.com", blood_group="ab+", admission_session="Fall 2026",
                      admission_category="Open Merit"),
    )
    assert resp.status_code == 201, resp.text
    detail = (await app_client.get(f"/api/v1/students/{resp.json()['id']}")).json()
    assert detail["email"] == "sana@example.com"
    assert detail["blood_group"] == "AB+"
    assert detail["admission_session"] == "Fall 2026"
    assert detail["admission_category"] == "Open Merit"


@pytest.mark.parametrize("bad", [{"blood_group": "Z+"}, {"email": "not-an-email"}])
async def test_invalid_new_attributes_rejected_with_422(app_client, admin_user, bad):
    await _login_admin(app_client)
    resp = await app_client.post("/api/v1/students", json=_payload(**bad))
    assert resp.status_code == 422


async def test_program_without_semester_defaults_to_semester_one(app_client, admin_user, db_session):
    program = await _program(db_session)
    await _login_admin(app_client)
    resp = await app_client.post("/api/v1/students", json=_payload(program_id=str(program.id)))
    assert resp.status_code == 201, resp.text
    detail = (await app_client.get(f"/api/v1/students/{resp.json()['id']}")).json()
    assert detail["current_semester"] == 1


async def test_new_admission_is_charged_active_fee_structure(app_client, admin_user, db_session):
    """A student admitted AFTER a fee structure was activated must still be billed."""
    program = await _program(db_session)
    await _login_admin(app_client)
    await _active_structure(app_client, program_id=str(program.id), semester=1)

    resp = await app_client.post("/api/v1/students", json=_payload(program_id=str(program.id)))
    assert resp.status_code == 201, resp.text
    rows = (
        await db_session.execute(select(FeeAssignment).where(FeeAssignment.payer_id == resp.json()["id"]))
    ).scalars().all()
    assert len(rows) == 1


async def test_program_or_semester_edit_triggers_fee_assignment(app_client, admin_user, db_session):
    program = await _program(db_session)
    await _login_admin(app_client)
    await _active_structure(app_client, program_id=str(program.id), semester=2)
    created = await app_client.post("/api/v1/students", json=_payload(program_id=str(program.id)))
    sid = created.json()["id"]
    before = (await db_session.execute(select(FeeAssignment).where(FeeAssignment.payer_id == sid))).scalars().all()
    assert before == []  # semester 1 student: structure is for semester 2

    patch = await app_client.patch(f"/api/v1/students/{sid}", json={"current_semester": 2})
    assert patch.status_code == 200, patch.text
    after = (await db_session.execute(select(FeeAssignment).where(FeeAssignment.payer_id == sid))).scalars().all()
    assert len(after) == 1


async def test_csv_import_sets_semester_new_columns_and_charges_fees(db_session, admin_user):
    program = await _program(db_session)
    header = (
        "first_name,last_name,date_of_birth,gender,cnic,contact_no,marital_status,religion,nationality,"
        "guardian_name,guardian_cnic,guardian_contact_no,relation_with_guardian,emergency_contact_no,"
        "present_address,province,district,city,program,enrollment_date,current_semester,email,"
        "blood_group,admission_session,admission_category\n"
    )
    row = (
        "Ayesha,Khan,2004-05-11,Female,12345-1234567-1,03001234567,Single,Islam,Pakistani,Muhammad Khan,"
        "12345-7654321-1,03007654321,Father,03009998888,House 12,Punjab,Lahore,Lahore,BSN,2026-08-01,"
        "3,ayesha@example.com,O-,Fall 2026,Self Finance\n"
    )
    data = (header + row).encode()
    svc = ImportService(db_session)
    job = await svc.create_job_from_upload(file_bytes=data, filename="s.csv", uploaded_by=admin_user.id)
    await db_session.commit()
    job = await svc.process_job(job.id, data)
    await db_session.commit()
    assert job.imported_rows == 1, job.status
    s = (await db_session.execute(select(Student))).scalar_one()
    assert s.current_semester == 3
    assert (s.email, s.blood_group, s.admission_session, s.admission_category) == (
        "ayesha@example.com", "O-", "Fall 2026", "Self Finance",
    )


async def test_csv_import_without_semester_defaults_to_one(db_session, admin_user):
    await _program(db_session)
    header = (
        "first_name,last_name,date_of_birth,gender,cnic,contact_no,marital_status,religion,nationality,"
        "guardian_name,guardian_cnic,guardian_contact_no,relation_with_guardian,emergency_contact_no,"
        "present_address,province,district,city,program\n"
    )
    row = (
        "Ali,Raza,2004-05-11,Male,12345-1234567-2,03001234568,Single,Islam,Pakistani,Raza Khan,"
        "12345-7654321-2,03007654322,Father,03009998889,House 1,Punjab,Lahore,Lahore,BSN\n"
    )
    data = (header + row).encode()
    svc = ImportService(db_session)
    job = await svc.create_job_from_upload(file_bytes=data, filename="s2.csv", uploaded_by=admin_user.id)
    await db_session.commit()
    job = await svc.process_job(job.id, data)
    await db_session.commit()
    assert job.imported_rows == 1
    assert (await db_session.execute(select(Student))).scalar_one().current_semester == 1


async def test_csv_semester_beyond_program_length_rejects_file(db_session, admin_user):
    await _program(db_session, semesters=4)
    header = (
        "first_name,last_name,date_of_birth,gender,cnic,contact_no,marital_status,religion,nationality,"
        "guardian_name,guardian_cnic,guardian_contact_no,relation_with_guardian,emergency_contact_no,"
        "present_address,province,district,city,program,current_semester\n"
    )
    row = (
        "Ali,Raza,2004-05-11,Male,12345-1234567-3,03001234569,Single,Islam,Pakistani,Raza Khan,"
        "12345-7654321-3,03007654323,Father,03009998880,House 1,Punjab,Lahore,Lahore,BSN,9\n"
    )
    data = (header + row).encode()
    svc = ImportService(db_session)
    job = await svc.create_job_from_upload(file_bytes=data, filename="s3.csv", uploaded_by=admin_user.id)
    await db_session.commit()
    job = await svc.process_job(job.id, data)
    await db_session.commit()
    assert job.imported_rows == 0 and job.invalid_rows == 1


# ---------------------------------------------------------------------------
# Advance semester safety, export, slip, directory RBAC
# ---------------------------------------------------------------------------
async def _make_student(client, cnic, **kw):
    r = await client.post("/api/v1/students", json=_payload(cnic=cnic, guardian_cnic="35202-9999999-9", **kw))
    assert r.status_code == 201, r.text
    return r.json()


async def test_advance_dry_run_changes_nothing_and_explains_holds(app_client, admin_user, db_session):
    program = await _program(db_session)
    await _login_admin(app_client)
    s1 = await _make_student(app_client, "35202-3000001-1", program_id=str(program.id), current_semester=1)
    await _make_student(app_client, "35202-3000002-2", program_id=str(program.id), current_semester=8)

    resp = await app_client.post("/api/v1/students/advance-semester", json={"dry_run": True})
    body = resp.json()
    assert resp.status_code == 200, resp.text
    assert body["dry_run"] is True and body["advanced_count"] == 1 and body["skipped_already_at_max"] == 1
    assert any("final semester" in x["reason"] for x in body["skipped_students"])
    detail = (await app_client.get(f"/api/v1/students/{s1['id']}")).json()
    assert detail["current_semester"] == 1  # untouched by a dry run


async def test_advance_holds_back_students_with_courses_in_progress(app_client, admin_user, db_session):
    from datetime import datetime, timezone

    from app.academics.models import Course, CourseOffering, Enrollment, EnrollmentStatus

    program = await _program(db_session)
    await _login_admin(app_client)
    held = await _make_student(app_client, "35202-3000003-3", program_id=str(program.id), current_semester=1)
    free = await _make_student(app_client, "35202-3000004-4", program_id=str(program.id), current_semester=1)

    course = Course(code="OPEN101", title="Open", credit_hours=3)
    db_session.add(course)
    await db_session.flush()
    offering = CourseOffering(course_id=course.id, term_label="Fall 2026")
    db_session.add(offering)
    await db_session.flush()
    db_session.add(Enrollment(student_id=held["id"], course_offering_id=offering.id,
                              status=EnrollmentStatus.ENROLLED, enrolled_at=datetime.now(timezone.utc)))
    await db_session.commit()

    body = (await app_client.post("/api/v1/students/advance-semester", json={})).json()
    assert body["advanced_count"] == 1 and body["skipped_open_enrollments"] == 1
    assert (await app_client.get(f"/api/v1/students/{held['id']}")).json()["current_semester"] == 1
    assert (await app_client.get(f"/api/v1/students/{free['id']}")).json()["current_semester"] == 2

    # Admin may knowingly override the hold.
    forced = (await app_client.post("/api/v1/students/advance-semester",
                                    json={"skip_open_enrollments": False})).json()
    assert forced["advanced_count"] >= 1


async def test_advance_can_hold_back_overdue_fees(app_client, admin_user, db_session):
    program = await _program(db_session)
    await _login_admin(app_client)
    ft = await app_client.post("/api/v1/fees/types", json={"code": "TUI", "name": "Tuition"})
    st = await app_client.post("/api/v1/fees/structures", json={
        "fee_type_id": ft.json()["id"], "name": "Old tuition", "term_label": "Spring 2020",
        "total_amount": 5000, "installment_count": 1, "effective_from": "2020-01-01",
    })
    assert (await app_client.post(f"/api/v1/fees/structures/{st.json()['id']}/activate")).status_code == 200
    await _make_student(app_client, "35202-3000005-5", program_id=str(program.id), current_semester=1)

    held = (await app_client.post("/api/v1/students/advance-semester",
                                  json={"skip_overdue_fees": True, "dry_run": True})).json()
    assert held["skipped_overdue_fees"] == 1 and held["advanced_count"] == 0
    allowed = (await app_client.post("/api/v1/students/advance-semester", json={"dry_run": True})).json()
    assert allowed["advanced_count"] == 1


async def test_export_csv_contains_students_and_neutralises_formulas(app_client, admin_user, db_session):
    await _login_admin(app_client)
    await _make_student(app_client, "35202-3000006-6", admission_category="=HYPERLINK(\"x\")")
    resp = await app_client.get("/api/v1/students/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    text = resp.content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("roll_number,first_name")
    assert "Sana" in text and "'=HYPERLINK" in text and ",=HYPERLINK" not in text


async def test_admission_slip_shows_password_only_until_first_login(app_client, admin_user, db_session):
    await _login_admin(app_client)
    created = await _make_student(app_client, "35202-3000007-7")
    slip = await app_client.get(f"/api/v1/students/{created['id']}/admission-slip")
    assert slip.status_code == 200 and created["username"] in slip.text and "vertex123" in slip.text

    await app_client.post("/api/v1/auth/login", json={"username": created["username"], "password": "vertex123"})
    assert (await app_client.post("/api/v1/auth/change-password",
            json={"current_password": "vertex123", "new_password": "NewStudent123!"})).status_code == 204
    await _login_admin(app_client)
    slip2 = await app_client.get(f"/api/v1/students/{created['id']}/admission-slip")
    assert "vertex123" not in slip2.text and "Already changed" in slip2.text


async def test_teacher_and_student_cannot_list_or_export_directory(app_client, admin_user, db_session, seeded_roles):
    from app.auth.models import User, UserRole
    from app.core.security import hash_password

    await _login_admin(app_client)
    created = await _make_student(app_client, "35202-3000008-8")
    teacher = User(username="t_dir", password_hash=hash_password("TeachPass123!"), must_change_password=False)
    db_session.add(teacher)
    await db_session.flush()
    db_session.add(UserRole(user_id=teacher.id, role_id=seeded_roles["TEACHER"].id))
    await db_session.commit()

    await app_client.post("/api/v1/auth/login", json={"username": "t_dir", "password": "TeachPass123!"})
    assert (await app_client.get("/api/v1/students")).status_code == 403
    assert (await app_client.get("/api/v1/students/export")).status_code == 403

    await app_client.post("/api/v1/auth/login", json={"username": created["username"], "password": "vertex123"})
    assert (await app_client.get("/api/v1/students")).status_code in (401, 403)


async def test_course_scoped_fee_is_not_charged_at_admission_or_advance(app_client, admin_user, db_session):
    """Regression: a course-scoped structure (lab fee) must only be charged on
    enrollment in that course, never by program/semester automation."""
    from app.academics.models import Course

    program = await _program(db_session)
    course = Course(code="LAB101", title="Lab", credit_hours=1)
    db_session.add(course)
    await db_session.commit()
    await _login_admin(app_client)
    ft = await app_client.post("/api/v1/fees/types", json={"code": "LAB", "name": "Lab Fee"})
    st = await app_client.post("/api/v1/fees/structures", json={
        "fee_type_id": ft.json()["id"], "name": "Lab fee", "term_label": "Fall 2026", "total_amount": 500,
        "installment_count": 1, "effective_from": "2026-09-01", "course_id": str(course.id),
    })
    assert st.status_code == 201, st.text
    assert (await app_client.post(f"/api/v1/fees/structures/{st.json()['id']}/activate")).status_code == 200

    created = await _make_student(app_client, "35202-3000009-9", program_id=str(program.id), current_semester=1)
    await app_client.post("/api/v1/students/advance-semester", json={})
    rows = (await db_session.execute(select(FeeAssignment).where(FeeAssignment.payer_id == created["id"]))).scalars().all()
    assert rows == []



async def test_student_full_profile_opens(app_client, admin_user, db_session):
    """Regression: 'View Full Profile' crashed for every student (a bad `from time import date`)."""
    program = await _program(db_session)
    await _login_admin(app_client)
    created = await _make_student(app_client, "35202-3100001-1", program_id=str(program.id), current_semester=1,
                                  email="fp@example.com", admission_session="Fall 2026")
    r = await app_client.get(f"/api/v1/students/{created['id']}/full-profile")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["roll_number"] == created["username"] and body["email"] == "fp@example.com"
    assert body["library"] == {"active_loans": 0, "overdue_loans": 0, "past_loans": 0}