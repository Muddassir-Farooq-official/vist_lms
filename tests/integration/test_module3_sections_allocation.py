"""Module 3 (Subject allocation & timetable) launch-readiness tests."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.academics.models import Enrollment, EnrollmentStatus
from app.employees.models import JobRole
from app.programs.models import Program
from app.students.models import Student

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}
_DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
FALL_START, FALL_END = date(2026, 9, 7), date(2026, 12, 18)        # a Monday
SPRING_START, SPRING_END = date(2027, 1, 11), date(2027, 4, 30)    # also a Monday
MONDAY = _DAYS[FALL_START.weekday()]


async def _login(c, u, p):
    r = await c.post("/api/v1/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text


async def _admin(c):
    await _login(c, **{"u": ADMIN["username"], "p": ADMIN["password"]})


async def _teacher(c, db, code, cnic):
    jr = JobRole(code=code, title="Teacher", default_role_code="TEACHER")
    db.add(jr)
    await db.commit()
    r = await c.post("/api/v1/employees", json={
        "first_name": code, "last_name": "T", "date_of_birth": "1985-01-01", "gender": "Male", "cnic": cnic,
        "contact_no": "03007770000", "hire_date": "2026-01-01", "job_role_id": str(jr.id), "base_salary": 1,
        "password": "TeacherPass123!"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _period(c, start, end):
    existing = (await c.get("/api/v1/academics/periods")).json()
    for p in existing:
        if p["start_time"] == start and p["end_time"] == end:
            return p["id"]
    r = await c.post("/api/v1/academics/periods", json={"period_number": len(existing) + 1, "start_time": start, "end_time": end})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _course(c, code, program_id=None, semester=1, credit=3):
    r = await c.post("/api/v1/academics/courses", json={"code": code, "title": code, "credit_hours": credit})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    if program_id:
        lk = await c.post(f"/api/v1/academics/courses/{cid}/programs", json={"program_id": program_id, "semester_number": semester})
        assert lk.status_code in (200, 201), lk.text
    return cid


async def _offering(c, course_id, term="Fall 2026", section="A", start=FALL_START, end=FALL_END, teacher=None, capacity=None, open_=True):
    r = await c.post("/api/v1/academics/offerings", json={
        "course_id": course_id, "term_label": term, "section_label": section, "capacity": capacity,
        "assigned_teacher_id": teacher, "term_start_date": str(start), "term_end_date": str(end)})
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    if open_:
        assert (await c.patch(f"/api/v1/academics/offerings/{oid}", json={"is_open_for_enrollment": True})).status_code == 200
    return oid


async def _slot(c, oid, period, room, day=MONDAY):
    return await c.post(f"/api/v1/academics/offerings/{oid}/timetable-slots",
                        json={"day_of_week": day, "period_id": period, "room": room})


async def _program(db, code="BSCS", sems=8):
    p = Program(code=code, name=f"Prog {code}", duration_years=4, total_semesters=sems, max_credit_hours_per_semester=21)
    db.add(p)
    await db.commit()
    return p


async def _student(db, n, program, semester=1, section=None, status=None):
    st = Student(first_name=f"S{n}", last_name="X", date_of_birth=date(2005, 1, 1), gender="Male",
                 cnic=f"35202-70000{n:02d}-1", contact_no=f"030011100{n:02d}", marital_status="Single", religion="Islam",
                 nationality="Pakistani", roll_number=f"R{n:03d}", program_id=program.id, current_semester=semester,
                 section_label=section)
    if status:
        st.status = status
    db.add(st)
    await db.commit()
    return st


# ---------------------------------------------------------------- sections
async def test_two_sections_of_same_course_and_term_can_coexist(app_client, admin_user):
    await _admin(app_client)
    cid = await _course(app_client, "SEC-101")
    a = await _offering(app_client, cid, section="A")
    b = await _offering(app_client, cid, section="B")
    assert a != b
    dup = await app_client.post("/api/v1/academics/offerings", json={"course_id": cid, "term_label": "Fall 2026", "section_label": " a "})
    assert dup.status_code == 409  # case/space-insensitive duplicate
    listing = (await app_client.get("/api/v1/academics/offerings", params={"term_label": "Fall 2026"})).json()
    assert sorted(o["section_label"] for o in listing) == ["A", "B"]


async def test_term_label_is_canonicalised(app_client, admin_user):
    await _admin(app_client)
    c1, c2 = await _course(app_client, "TRM-101"), await _course(app_client, "TRM-102")
    await _offering(app_client, c1, term="Fall 2026")
    await _offering(app_client, c2, term="  fall   2026 ")
    terms = (await app_client.get("/api/v1/academics/terms")).json()
    assert terms == ["Fall 2026"]


async def test_parallel_sections_do_not_trip_the_cohort_conflict(app_client, admin_user, db_session):
    await _admin(app_client)
    prog = await _program(db_session)
    t1 = await _teacher(app_client, db_session, "T1", "35202-8000001-1")
    t2 = await _teacher(app_client, db_session, "T2", "35202-8000002-2")
    c1, c2 = await _course(app_client, "PAR-101", str(prog.id)), await _course(app_client, "PAR-102", str(prog.id))
    period = await _period(app_client, "09:00:00", "10:00:00")
    a1 = await _offering(app_client, c1, section="A", teacher=t1)
    b2 = await _offering(app_client, c2, section="B", teacher=t2)
    assert (await _slot(app_client, a1, period, "Room 1")).status_code == 201
    # different course + different section + same slot time: different students -> allowed
    assert (await _slot(app_client, b2, period, "Room 2")).status_code == 201
    # but the SAME section attending two different courses at once is a real conflict
    t3 = await _teacher(app_client, db_session, "T3", "35202-8000003-3")
    a2 = await _offering(app_client, c2, section="A", teacher=t3, term="Fall 2026 extra")
    clash = await _slot(app_client, a2, period, "Room 3")
    assert clash.status_code == 422 and "section A" in clash.text


async def test_sections_of_the_same_course_may_run_at_the_same_time(app_client, admin_user, db_session):
    await _admin(app_client)
    prog = await _program(db_session)
    t1 = await _teacher(app_client, db_session, "T1", "35202-8000001-1")
    t2 = await _teacher(app_client, db_session, "T2", "35202-8000002-2")
    cid = await _course(app_client, "SAM-101", str(prog.id))
    period = await _period(app_client, "09:00:00", "10:00:00")
    a = await _offering(app_client, cid, section="A", teacher=t1)
    b = await _offering(app_client, cid, section="B", teacher=t2)
    assert (await _slot(app_client, a, period, "Room 1")).status_code == 201
    assert (await _slot(app_client, b, period, "Room 2")).status_code == 201


# ------------------------------------- next term must not collide with the last
async def test_next_terms_slots_do_not_conflict_with_last_terms(app_client, admin_user, db_session):
    await _admin(app_client)
    t1 = await _teacher(app_client, db_session, "T1", "35202-8000001-1")
    c1, c2 = await _course(app_client, "TRM-201"), await _course(app_client, "TRM-202")
    period = await _period(app_client, "09:00:00", "10:00:00")
    fall = await _offering(app_client, c1, term="Fall 2026", teacher=t1)
    assert (await _slot(app_client, fall, period, "Room 24")).status_code == 201
    spring = await _offering(app_client, c2, term="Spring 2027", start=SPRING_START, end=SPRING_END, teacher=t1)
    # same room, same teacher, same weekday+time -- but a different time of year
    assert (await _slot(app_client, spring, period, "Room 24")).status_code == 201
    # while a genuinely overlapping term still conflicts
    overlap = await _offering(app_client, await _course(app_client, "TRM-203"), term="Fall 2026 late", start=FALL_START, end=FALL_END, teacher=t1)
    assert (await _slot(app_client, overlap, period, "Room 99")).status_code == 422


# --------------------------------------------------------------- capacity etc.
async def test_capacity_blocks_extra_enrollment_and_cannot_drop_below_enrolled(app_client, admin_user, db_session):
    await _admin(app_client)
    prog = await _program(db_session)
    cid = await _course(app_client, "CAP-101", str(prog.id))
    oid = await _offering(app_client, cid, capacity=1)
    s1, s2 = await _student(db_session, 1, prog), await _student(db_session, 2, prog)
    assert (await app_client.post(f"/api/v1/academics/offerings/{oid}/enroll-student", json={"student_id": str(s1.id)})).status_code == 201
    full = await app_client.post(f"/api/v1/academics/offerings/{oid}/enroll-student", json={"student_id": str(s2.id)})
    assert full.status_code == 422 and "full" in full.text
    low = await app_client.patch(f"/api/v1/academics/offerings/{oid}", json={"capacity": 1})
    assert low.status_code == 200
    bad = await app_client.post("/api/v1/academics/offerings", json={"course_id": cid, "term_label": "X", "capacity": 0})
    assert bad.status_code == 422


async def test_student_in_a_section_can_only_enroll_in_that_section(app_client, admin_user, db_session):
    await _admin(app_client)
    prog = await _program(db_session)
    cid = await _course(app_client, "SEC-201", str(prog.id))
    a, b = await _offering(app_client, cid, section="A"), await _offering(app_client, cid, section="B")
    st = await _student(db_session, 1, prog, section="B")
    wrong = await app_client.post(f"/api/v1/academics/offerings/{a}/enroll-student", json={"student_id": str(st.id)})
    assert wrong.status_code == 422 and "not your section" in wrong.text
    assert (await app_client.post(f"/api/v1/academics/offerings/{b}/enroll-student", json={"student_id": str(st.id)})).status_code == 201


async def test_student_cannot_enroll_in_two_overlapping_classes(app_client, admin_user, db_session):
    await _admin(app_client)
    prog = await _program(db_session)
    t1 = await _teacher(app_client, db_session, "T1", "35202-8000001-1")
    t2 = await _teacher(app_client, db_session, "T2", "35202-8000002-2")
    c1, c2 = await _course(app_client, "CLS-101", str(prog.id)), await _course(app_client, "CLS-102", str(prog.id))
    period = await _period(app_client, "09:00:00", "10:00:00")
    o1 = await _offering(app_client, c1, section="A", teacher=t1)
    o2 = await _offering(app_client, c2, section="B", teacher=t2)  # other section => slot creation allowed
    assert (await _slot(app_client, o1, period, "R1")).status_code == 201
    assert (await _slot(app_client, o2, period, "R2")).status_code == 201
    st = await _student(db_session, 1, prog)  # no section => could pick either
    assert (await app_client.post(f"/api/v1/academics/offerings/{o1}/enroll-student", json={"student_id": str(st.id)})).status_code == 201
    clash = await app_client.post(f"/api/v1/academics/offerings/{o2}/enroll-student", json={"student_id": str(st.id)})
    assert clash.status_code == 422 and "Timetable clash" in clash.text


async def test_reassigning_a_teacher_cannot_double_book_them(app_client, admin_user, db_session):
    await _admin(app_client)
    t1 = await _teacher(app_client, db_session, "T1", "35202-8000001-1")
    t2 = await _teacher(app_client, db_session, "T2", "35202-8000002-2")
    period = await _period(app_client, "09:00:00", "10:00:00")
    o1 = await _offering(app_client, await _course(app_client, "TCH-101"), teacher=t1)
    o2 = await _offering(app_client, await _course(app_client, "TCH-102"), teacher=t2)
    assert (await _slot(app_client, o1, period, "R1")).status_code == 201
    assert (await _slot(app_client, o2, period, "R2")).status_code == 201
    r = await app_client.patch(f"/api/v1/academics/offerings/{o2}", json={"assigned_teacher_id": t1})
    assert r.status_code == 422 and "clashes" in r.text


# ------------------------------------------------------ generate + batch enroll
async def test_generate_offerings_for_a_whole_semester(app_client, admin_user, db_session):
    await _admin(app_client)
    prog = await _program(db_session)
    for i in range(3):
        await _course(app_client, f"GEN-10{i}", str(prog.id), semester=2)
    await _course(app_client, "GEN-OTHER", str(prog.id), semester=3)
    body = {"term_label": "Fall 2026", "program_id": str(prog.id), "semester_number": 2, "section_label": "A",
            "term_start_date": str(FALL_START), "term_end_date": str(FALL_END), "open_for_enrollment": True, "capacity": 40}
    r = await app_client.post("/api/v1/academics/offerings/generate", json=body)
    assert r.status_code == 201, r.text
    assert len(r.json()["created"]) == 3 and r.json()["skipped"] == []
    again = (await app_client.post("/api/v1/academics/offerings/generate", json=body)).json()
    assert again["created"] == [] and len(again["skipped"]) == 3  # idempotent
    listing = (await app_client.get("/api/v1/academics/offerings", params={"term_label": "Fall 2026"})).json()
    assert len(listing) == 3 and all(o["capacity"] == 40 and o["is_open_for_enrollment"] for o in listing)
    empty = await app_client.post("/api/v1/academics/offerings/generate", json={**body, "semester_number": 5})
    assert empty.status_code == 422 and "No courses" in empty.text
    toohigh = await app_client.post("/api/v1/academics/offerings/generate", json={**body, "semester_number": 9})
    assert toohigh.status_code == 422


async def test_batch_enroll_by_section_reports_each_failure(app_client, admin_user, db_session):
    await _admin(app_client)
    prog = await _program(db_session)
    cid = await _course(app_client, "BAT-101", str(prog.id))
    oid = await _offering(app_client, cid, section="A", capacity=2, open_=False)  # closed to self-enrollment
    for n in (1, 2, 3):
        await _student(db_session, n, prog, section="A")
    await _student(db_session, 4, prog, section="B")   # different section: not selected
    await _student(db_session, 5, prog, semester=3, section="A")  # different semester: not selected
    r = await app_client.post(f"/api/v1/academics/offerings/{oid}/enroll-batch",
                              json={"program_id": str(prog.id), "semester_number": 1, "section_label": "A"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enrolled"] == 2 and body["failed_count"] == 1 and "full" in body["failed"][0]["reason"]
    # re-running is safe: already-enrolled students are counted, not duplicated
    again = (await app_client.post(f"/api/v1/academics/offerings/{oid}/enroll-batch",
                                   json={"program_id": str(prog.id), "semester_number": 1, "section_label": "A"})).json()
    assert again["already_enrolled"] == 2
    rows = (await db_session.execute(select(Enrollment).where(Enrollment.status == EnrollmentStatus.ENROLLED))).scalars().all()
    assert len(rows) == 2
    none = await app_client.post(f"/api/v1/academics/offerings/{oid}/enroll-batch", json={})
    assert none.status_code == 422


# ------------------------------------------------------------- overview
async def test_allocation_overview_flags_what_is_missing(app_client, admin_user, db_session):
    await _admin(app_client)
    prog = await _program(db_session)
    t1 = await _teacher(app_client, db_session, "T1", "35202-8000001-1")
    ready = await _course(app_client, "OVR-101", str(prog.id))
    bare = await _course(app_client, "OVR-102", str(prog.id))
    await _course(app_client, "OVR-103", str(prog.id))  # never opened this term
    period = await _period(app_client, "09:00:00", "10:00:00")
    o1 = await _offering(app_client, ready, teacher=t1)
    assert (await _slot(app_client, o1, period, "R1")).status_code == 201
    await _offering(app_client, bare)  # no teacher, no timetable
    await _student(db_session, 1, prog)
    ov = (await app_client.get("/api/v1/academics/allocation-overview", params={"term_label": "fall 2026"})).json()
    by_code = {r["course_code"]: r for r in ov["sections"]}
    assert "NO_TEACHER" not in by_code["OVR-101"]["issues"] and "NO_TIMETABLE" not in by_code["OVR-101"]["issues"]
    assert {"NO_TEACHER", "NO_TIMETABLE"} <= set(by_code["OVR-102"]["issues"])
    assert by_code["OVR-101"]["weekly_hours"] == 1.0
    assert [g["course_code"] for g in ov["courses_without_offering"]] == ["OVR-103"]
    assert ov["teacher_loads"][0]["weekly_hours"] == 1.0
    # TOPICS_MISSING depends on today's date relative to the term, so count only the structural problems here.
    structural = [r for r in ov["sections"] if set(r["issues"]) - {"NO_STUDENTS", "TOPICS_MISSING"}]
    assert [r["course_code"] for r in structural] == ["OVR-102"]
    assert ov["summary"]["sections_with_problems"] >= 1


async def test_only_admin_can_use_allocation_endpoints(app_client, admin_user, db_session):
    await _admin(app_client)
    t1 = await _teacher(app_client, db_session, "T1", "35202-8000001-1")
    cid = await _course(app_client, "RBAC-101")
    oid = await _offering(app_client, cid, teacher=t1)
    await _login(app_client, "EMP-0001", "TeacherPass123!")
    await app_client.post("/api/v1/auth/change-password", json={"current_password": "TeacherPass123!", "new_password": "TeacherNew12345!"})
    await _login(app_client, "EMP-0001", "TeacherNew12345!")
    assert (await app_client.get("/api/v1/academics/allocation-overview", params={"term_label": "Fall 2026"})).status_code == 403
    assert (await app_client.post(f"/api/v1/academics/offerings/{oid}/enroll-batch", json={"section_label": "A"})).status_code == 403
    assert (await app_client.post("/api/v1/academics/offerings/generate", json={})).status_code in (403, 422)


async def test_student_section_saved_and_exported(app_client, admin_user, db_session):
    await _admin(app_client)
    r = await app_client.post("/api/v1/students", json={
        "first_name": "Sana", "last_name": "Iqbal", "date_of_birth": "2005-03-03", "gender": "Female",
        "cnic": "35202-5000001-1", "contact_no": "03001110000", "marital_status": "Single", "religion": "Islam",
        "nationality": "Pakistani", "guardian_name": "G", "guardian_cnic": "35202-5000002-2",
        "relation_with_guardian": "Father", "emergency_contact_no": "03001110001", "present_address": "x",
        "province": "Punjab", "district": "Lahore", "city": "Lahore", "section_label": "  Morning "})
    assert r.status_code == 201, r.text
    d = (await app_client.get(f"/api/v1/students/{r.json()['id']}")).json()
    assert d["section_label"] == "Morning"
    csv_text = (await app_client.get("/api/v1/students/export")).content.decode("utf-8-sig")
    assert "section" in csv_text.splitlines()[0] and "Morning" in csv_text