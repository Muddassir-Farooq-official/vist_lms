"""Module 2 (Employees) launch-readiness tests."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.auth.models import Role, UserRole
from app.employees.models import JobRole

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


async def _login(client, username, password):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text


async def _role(db_session, code, title, system_role):
    jr = JobRole(code=code, title=title, default_role_code=system_role)
    db_session.add(jr)
    await db_session.commit()
    return jr


def _payload(job_role_id, **kw):
    p = {
        "first_name": "Ali", "last_name": "Raza", "date_of_birth": "1990-05-01", "gender": "Male",
        "cnic": "35202-4000001-1", "contact_no": "03001112233", "hire_date": "2026-08-01",
        "department": "CS", "job_role_id": str(job_role_id), "base_salary": 50000,
        "password": "StaffPass12345!",
    }
    p.update(kw)
    return p


async def _create(client, jr, **kw):
    r = await client.post("/api/v1/employees", json=_payload(jr.id, **kw))
    assert r.status_code == 201, r.text
    return r.json()


async def _system_roles(db_session, employee_id):
    from app.employees.models import Employee

    e = await db_session.get(Employee, employee_id)
    rows = await db_session.execute(
        select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == e.user_id)
    )
    return sorted(r for (r,) in rows.all())


async def test_new_attributes_saved_validated_and_returned(app_client, admin_user, db_session):
    jr = await _role(db_session, "LECT", "Lecturer", "TEACHER")
    await _login(app_client, **ADMIN)
    emp = await _create(
        app_client, jr, email="Ali@Example.com", blood_group="o-", marital_status="married",
        employment_type="part time", emergency_contact_name="Sara Raza", emergency_contact_no="03005556666",
    )
    d = (await app_client.get(f"/api/v1/employees/{emp['id']}")).json()
    assert (d["email"], d["blood_group"], d["marital_status"], d["employment_type"]) == (
        "ali@example.com", "O-", "Married", "PART_TIME",
    )
    assert d["emergency_contact_no"] == "03005556666"
    fp = (await app_client.get(f"/api/v1/employees/{emp['id']}/full-profile")).json()
    assert fp["email"] == "ali@example.com" and fp["employment_type"] == "PART_TIME"


@pytest.mark.parametrize("bad", [
    {"email": "nope"}, {"blood_group": "Q+"}, {"marital_status": "Complicated"},
    {"employment_type": "SLAVE"}, {"emergency_contact_no": "123"},
])
async def test_invalid_new_attributes_are_422(app_client, admin_user, db_session, bad):
    jr = await _role(db_session, "LECT", "Lecturer", "TEACHER")
    await _login(app_client, **ADMIN)
    r = await app_client.post("/api/v1/employees", json=_payload(jr.id, **bad))
    assert r.status_code == 422


async def test_admin_can_correct_name_dob_gender_hire_date(app_client, admin_user, db_session):
    jr = await _role(db_session, "LECT", "Lecturer", "TEACHER")
    await _login(app_client, **ADMIN)
    emp = await _create(app_client, jr)
    r = await app_client.patch(
        f"/api/v1/employees/{emp['id']}",
        json={"first_name": "Ahmad", "last_name": "Rezaa", "date_of_birth": "1991-06-02",
              "gender": "male", "hire_date": "2026-07-15", "email": "a@b.co"},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["first_name"], d["last_name"], d["date_of_birth"], d["gender"], d["hire_date"]) == (
        "Ahmad", "Rezaa", "1991-06-02", "Male", "2026-07-15",
    )
    # immutable identifiers are untouched
    assert d["employee_code"] == emp["employee_code"] and d["cnic"] == "35202-4000001-1"


async def test_student_name_dob_gender_can_be_corrected(app_client, admin_user, db_session):
    await _login(app_client, **ADMIN)
    r = await app_client.post("/api/v1/students", json={
        "first_name": "Sana", "last_name": "Iqbal", "date_of_birth": "2005-03-03", "gender": "Female",
        "cnic": "35202-5000001-1", "contact_no": "03001110000", "marital_status": "Single", "religion": "Islam",
        "nationality": "Pakistani", "guardian_name": "G", "guardian_cnic": "35202-5000002-2",
        "relation_with_guardian": "Father", "emergency_contact_no": "03001110001", "present_address": "x",
        "province": "Punjab", "district": "Lahore", "city": "Lahore",
    })
    sid = r.json()["id"]
    p = await app_client.patch(f"/api/v1/students/{sid}", json={"first_name": "Sanaa", "gender": "male", "date_of_birth": "2004-12-31"})
    assert p.status_code == 200, p.text
    assert (p.json()["first_name"], p.json()["gender"], p.json()["date_of_birth"]) == ("Sanaa", "Male", "2004-12-31")


async def test_change_job_role_swaps_login_role_and_forces_relogin(app_client, admin_user, db_session):
    teacher = await _role(db_session, "LECT", "Lecturer", "TEACHER")
    librarian = await _role(db_session, "LIBR", "Librarian", "LIBRARIAN")
    await _login(app_client, **ADMIN)
    emp = await _create(app_client, librarian)
    assert await _system_roles(db_session, emp["id"]) == ["LIBRARIAN"]

    r = await app_client.post(f"/api/v1/employees/{emp['id']}/job-role", json={"new_job_role_id": str(teacher.id), "reason": "Promoted"})
    assert r.status_code == 200, r.text
    assert r.json()["job_role_id"] == str(teacher.id)
    assert await _system_roles(db_session, emp["id"]) == ["TEACHER"]


async def test_change_job_role_keeps_admin_role_untouched(app_client, admin_user, db_session):
    teacher = await _role(db_session, "LECT", "Lecturer", "TEACHER")
    librarian = await _role(db_session, "LIBR", "Librarian", "LIBRARIAN")
    await _login(app_client, **ADMIN)
    emp = await _create(app_client, librarian)
    from app.employees.models import Employee

    e = await db_session.get(Employee, emp["id"])
    admin_role = (await db_session.execute(select(Role).where(Role.code == "ADMIN"))).scalar_one()
    db_session.add(UserRole(user_id=e.user_id, role_id=admin_role.id))
    await db_session.commit()

    r = await app_client.post(f"/api/v1/employees/{emp['id']}/job-role", json={"new_job_role_id": str(teacher.id)})
    assert r.status_code == 200, r.text
    assert await _system_roles(db_session, emp["id"]) == ["ADMIN", "TEACHER"]


async def test_change_job_role_rules(app_client, admin_user, db_session):
    teacher = await _role(db_session, "LECT", "Lecturer", "TEACHER")
    librarian = await _role(db_session, "LIBR", "Librarian", "LIBRARIAN")
    dead = JobRole(code="OLD", title="Old", default_role_code="LIBRARIAN", is_active=False)
    db_session.add(dead)
    await db_session.commit()
    await _login(app_client, **ADMIN)
    emp = await _create(app_client, teacher)
    url = f"/api/v1/employees/{emp['id']}/job-role"

    assert (await app_client.post(url, json={"new_job_role_id": str(teacher.id)})).status_code == 422  # same role
    assert (await app_client.post(url, json={"new_job_role_id": str(dead.id)})).status_code == 422  # inactive
    assert (await app_client.post(url, json={"new_job_role_id": "00000000-0000-0000-0000-000000000000"})).status_code == 404

    # teacher with a RUNNING course cannot be moved away from teaching
    course = await app_client.post("/api/v1/academics/courses", json={"code": "JR-101", "title": "C", "credit_hours": 3})
    off = await app_client.post("/api/v1/academics/offerings", json={
        "course_id": course.json()["id"], "term_label": "Fall 2026", "assigned_teacher_id": emp["id"]})
    assert off.status_code == 201, off.text
    blocked = await app_client.post(url, json={"new_job_role_id": str(librarian.id)})
    assert blocked.status_code == 422 and "still assigned" in blocked.text

    # after the course is reassigned (unassigned), the move is allowed
    await app_client.patch(f"/api/v1/academics/offerings/{off.json()['id']}", json={"assigned_teacher_id": None})
    assert (await app_client.post(url, json={"new_job_role_id": str(librarian.id)})).status_code == 200

    # terminated staff cannot be re-titled
    await app_client.post(f"/api/v1/employees/{emp['id']}/status", json={"new_status": "TERMINATED"})
    assert (await app_client.post(url, json={"new_job_role_id": str(teacher.id)})).status_code == 422


async def test_clearance_report(app_client, admin_user, db_session):
    teacher = await _role(db_session, "LECT", "Lecturer", "TEACHER")
    await _login(app_client, **ADMIN)
    emp = await _create(app_client, teacher)
    clean = (await app_client.get(f"/api/v1/employees/{emp['id']}/clearance")).json()
    assert clean["can_leave_cleanly"] is True and clean["active_teaching_offerings"] == 0

    course = await app_client.post("/api/v1/academics/courses", json={"code": "CL-101", "title": "C", "credit_hours": 3})
    await app_client.post("/api/v1/academics/offerings", json={
        "course_id": course.json()["id"], "term_label": "Fall 2026", "assigned_teacher_id": emp["id"]})
    busy = (await app_client.get(f"/api/v1/employees/{emp['id']}/clearance")).json()
    assert busy["active_teaching_offerings"] == 1 and busy["can_leave_cleanly"] is False


async def test_export_csv(app_client, admin_user, db_session):
    jr = await _role(db_session, "LECT", "Lecturer", "TEACHER")
    await _login(app_client, **ADMIN)
    await _create(app_client, jr, department='=cmd|"x"')
    r = await app_client.get("/api/v1/employees/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    text = r.content.decode("utf-8-sig")
    assert text.startswith("employee_code,first_name") and "Lecturer" in text and "'=cmd" in text


async def test_import_template_and_successful_import(app_client, admin_user, db_session):
    await _role(db_session, "LECT", "Lecturer", "TEACHER")
    await _role(db_session, "LIBR", "Librarian", "LIBRARIAN")
    await _login(app_client, **ADMIN)
    tpl = await app_client.get("/api/v1/employees/import-template")
    assert tpl.status_code == 200 and tpl.text.startswith("first_name,last_name")

    csv_text = (
        "first_name,last_name,date_of_birth,gender,cnic,contact_no,hire_date,job_role_code,base_salary,department,email,employment_type\n"
        "Ayesha,Khan,1988-04-12,Female,35202-6000001-1,03001234567,2026-08-01,LECT,85000,CS,ay@x.co,PERMANENT\n"
        "Bilal,Ahmed,15/03/1985,Male,35202-6000002-2,03007654321,01/08/2026,libr,60000,Library,,CONTRACT\n"
    )
    r = await app_client.post(
        "/api/v1/employees/import", files={"file": ("staff.csv", csv_text.encode(), "text/csv")},
        data={"initial_password": "Welcome@Staff1"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 2
    codes = [e["employee_code"] for e in r.json()["employees"]]
    # imported staff can log in with the shared initial password and MUST change it
    await _login(app_client, codes[0], "Welcome@Staff1")
    me = await app_client.get("/api/v1/auth/me")
    assert me.json()["must_change_password"] is True
    assert "TEACHER" in me.json()["roles"]


async def test_import_is_all_or_nothing_with_row_level_errors(app_client, admin_user, db_session):
    await _role(db_session, "LECT", "Lecturer", "TEACHER")
    await _login(app_client, **ADMIN)
    csv_text = (
        "first_name,last_name,date_of_birth,gender,cnic,contact_no,hire_date,job_role_code,base_salary\n"
        "Good,Row,1988-04-12,Female,35202-6100001-1,03001234567,2026-08-01,LECT,85000\n"
        "Bad,Cnic,1988-04-12,Female,not-a-cnic,03001234567,2026-08-01,LECT,85000\n"
        "Bad,Role,1988-04-12,Female,35202-6100003-3,03001234567,2026-08-01,NOPE,85000\n"
        "Dup,Cnic,1988-04-12,Female,35202-6100001-1,03001234567,2026-08-01,LECT,85000\n"
    )
    r = await app_client.post(
        "/api/v1/employees/import", files={"file": ("s.csv", csv_text.encode(), "text/csv")},
        data={"initial_password": "Welcome@Staff1"},
    )
    assert r.status_code == 422
    rows = {x["row"] for x in r.json()["error"]["details"]["rows"]}
    assert rows == {3, 4, 5}
    listing = (await app_client.get("/api/v1/employees")).json()
    assert listing["total"] == 0  # nothing was created


async def test_import_rejects_bad_header_and_short_password(app_client, admin_user, db_session):
    await _role(db_session, "LECT", "Lecturer", "TEACHER")
    await _login(app_client, **ADMIN)
    r = await app_client.post("/api/v1/employees/import", files={"file": ("s.csv", b"first_name\nx\n", "text/csv")},
                              data={"initial_password": "Welcome@Staff1"})
    assert r.status_code == 422 and "Missing required" in r.text
    r2 = await app_client.post("/api/v1/employees/import", files={"file": ("s.csv", b"a\n", "text/csv")},
                               data={"initial_password": "short"})
    assert r2.status_code == 422


async def test_non_admin_cannot_use_employee_admin_endpoints(app_client, admin_user, db_session):
    teacher = await _role(db_session, "LECT", "Lecturer", "TEACHER")
    await _login(app_client, **ADMIN)
    emp = await _create(app_client, teacher, cnic="35202-4000009-9")
    await _login(app_client, emp["username"], "StaffPass12345!")
    await app_client.post("/api/v1/auth/change-password", json={"current_password": "StaffPass12345!", "new_password": "NewStaffPass123!"})
    await _login(app_client, emp["username"], "NewStaffPass123!")
    for method, url in [
        ("get", "/api/v1/employees"), ("get", "/api/v1/employees/export"),
        ("get", f"/api/v1/employees/{emp['id']}/clearance"), ("get", "/api/v1/employees/import-template"),
        ("post", f"/api/v1/employees/{emp['id']}/job-role"),
    ]:
        resp = await getattr(app_client, method)(url, **({"json": {"new_job_role_id": str(teacher.id)}} if method == "post" else {}))
        assert resp.status_code == 403, (url, resp.status_code)


async def test_terminated_employee_cannot_log_in_or_refresh(app_client, admin_user, db_session):
    teacher = await _role(db_session, "LECT", "Lecturer", "TEACHER")
    await _login(app_client, **ADMIN)
    emp = await _create(app_client, teacher, cnic="35202-4000010-1")
    assert (await app_client.post(f"/api/v1/employees/{emp['id']}/status", json={"new_status": "TERMINATED", "reason": "Resigned"})).status_code == 200
    r = await app_client.post("/api/v1/auth/login", json={"username": emp["username"], "password": "StaffPass12345!"})
    assert r.status_code in (401, 403)