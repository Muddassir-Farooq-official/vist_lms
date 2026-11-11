"""School mode, stage 1: set up classes / subjects / sections for a session, class and subject teachers,
placing students (auto-enrolment in the section's subjects), and school wording in the menus."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.academics.models import CourseOffering, Enrollment, EnrollmentStatus
from app.core.config import settings
from app.notifications.models import Notification
from app.programs.models import Program
from app.students.models import Student
from tests.integration.test_announcements import _staff
from tests.integration.test_attendance_and_grading import _login_admin, _student_payload

pytestmark = pytest.mark.asyncio
API = "/api/v1/school"
SESSION = {"term_label": "2026-27", "start_date": "2026-04-01", "end_date": "2027-03-31"}


@pytest.fixture(autouse=True)
def _q(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


@pytest.fixture
def school(monkeypatch):
    monkeypatch.setattr(settings, "school_mode", 1)


async def _setup(client, groups, sections=("A",)):
    r = await client.post(f"{API}/setup", json={**SESSION, "groups": list(groups), "sections": list(sections)})
    assert r.status_code == 200, r.text
    return r.json()


async def _classes(client):
    return {c["code"]: c for c in (await client.get(f"{API}/overview", params={"session_label": "2026-27"})).json()["classes"]}


async def _pupil(client, n, **extra):
    r = await client.post("/api/v1/students", json=_student_payload(first_name=f"Pupil{n}", cnic=f"35202-71000{n:02d}-1",
                                                                    guardian_cnic=f"35202-72000{n:02d}-2", **extra))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_setup_needs_school_mode_then_builds_classes_subjects_sections(app_client, admin_user, db_session, monkeypatch):
    await _login_admin(app_client)
    off = await app_client.post(f"{API}/setup", json={**SESSION, "groups": ["PRIMARY"]})
    assert off.status_code == 422 and "School mode is off" in off.text

    monkeypatch.setattr(settings, "school_mode", 1)
    first = await _setup(app_client, ["PRE", "PRIMARY", "MATRIC_SCI", "O_LEVEL"], ["A", "b"])
    assert first["session"] == "2026-27" and first["created"]["classes"] == 13 and first["created"]["sections"] == 26
    again = await _setup(app_client, ["PRE", "PRIMARY", "MATRIC_SCI", "O_LEVEL"], ["A", "B"])
    assert again["created"] == {"classes": 0, "subjects": 0, "sections": 0, "subject_classes": 0}           # safe to run twice

    classes = await _classes(app_client)
    order = [c["code"] for c in (await app_client.get(f"{API}/overview")).json()["classes"]]
    assert order[:4] == ["PG", "NUR", "KG", "C1"] and order.index("C5") < order.index("C9S")
    assert classes["KG"]["next_class"] == "Class 1" and classes["C9S"]["next_class"] == "Class 10 (Science)"
    assert classes["C5"]["next_class"] is None                                             # Class 6 is not set up
    assert [s["label"] for s in classes["C1"]["sections"]] == ["A", "B"]
    assert [s["title"] for s in classes["C1"]["subjects"]] == ["English", "Urdu", "Mathematics", "General Knowledge", "Islamiat", "Computer"]
    assert {"Physics", "Biology", "Tarjuma-tul-Quran"} <= {s["title"] for s in classes["C9S"]["subjects"]}
    o1 = {s["title"]: s["optional"] for s in classes["O1"]["subjects"]}
    assert o1["Physics"] is True and o1["English"] is False
    p = (await db_session.execute(select(Program).where(Program.code == "C9S"))).scalar_one()
    assert (p.school_stage, p.stream, p.total_semesters) == ("MATRIC", "Science", 1)
    n_subjects = sum(len(c["subjects"]) for c in classes.values())
    n_offerings = (await db_session.execute(select(func.count()).select_from(CourseOffering).where(CourseOffering.term_label == "2026-27"))).scalar_one()
    assert n_offerings == n_subjects * 2                                                     # every subject, in both sections
    start = (await db_session.execute(select(CourseOffering.term_start_date).where(CourseOffering.term_label == "2026-27").limit(1))).scalar_one()
    assert start == date(2026, 4, 1)


async def test_class_and_subject_teachers_sections_and_subjects(app_client, admin_user, db_session, school):
    await _login_admin(app_client)
    await _setup(app_client, ["PRIMARY"])
    t_user, _ = await _staff(app_client, db_session, "SCT1", "TEACHER", "35202-7300001-1")
    teachers = (await app_client.get(f"{API}/teachers")).json()
    teacher = next(t for t in teachers if t["name"].startswith("SCT1"))
    c5 = (await _classes(app_client))["C5"]
    sec_a = c5["sections"][0]["id"]

    r = await app_client.patch(f"{API}/sections/{sec_a}", json={"class_teacher_id": teacher["id"], "room": "Room 12", "capacity": 2})
    assert r.status_code == 200 and r.json()["class_teacher"].startswith("SCT1") and r.json()["room"] == "Room 12"
    from app.auth.models import User

    uid = (await db_session.execute(select(User.id).where(User.username == t_user))).scalar_one()
    titles = [n.title for n in (await db_session.execute(select(Notification).where(Notification.user_id == uid))).scalars()]
    assert "You are class teacher of Class 5 - A" in titles

    maths = next(s for s in r.json()["subjects"] if s["title"] == "Mathematics")
    r = await app_client.put(f"{API}/sections/{sec_a}/teachers", json={"teachers": {maths["course_id"]: teacher["id"]}})
    assert next(s for s in r.json()["subjects"] if s["title"] == "Mathematics")["teacher"].startswith("SCT1")
    off = (await db_session.execute(select(CourseOffering).where(CourseOffering.id == uuid.UUID(maths["offering_id"])))).scalar_one()
    assert str(off.assigned_teacher_id) == teacher["id"]

    added = await app_client.post(f"{API}/classes/{c5['id']}/subjects", json={"title": "Arabic", "code": "ARB", "optional": True})
    assert added.status_code == 201 and added.json()["code"] == "C5-ARB"
    assert (await app_client.post(f"{API}/classes/{c5['id']}/subjects", json={"title": "arabic"})).status_code == 409
    sec_b = await app_client.post(f"{API}/classes/{c5['id']}/sections", json={"label": "b"})
    assert sec_b.status_code == 201 and sec_b.json()["label"] == "B" and len(sec_b.json()["subjects"]) == 8
    assert (await app_client.post(f"{API}/classes/{c5['id']}/sections", json={"label": "B"})).status_code == 409
    assert (await app_client.delete(f"{API}/sections/{sec_b.json()['id']}")).status_code == 204
    assert (await app_client.delete(f"{API}/classes/{c5['id']}/subjects/{added.json()['course_id']}")).status_code == 204
    assert [s["title"] for s in (await _classes(app_client))["C5"]["subjects"]][-1] == "Computer"


async def test_placing_students_enrols_them_and_moving_sections(app_client, admin_user, db_session, school):
    await _login_admin(app_client)
    await _setup(app_client, ["O_LEVEL"], ["A", "B"])
    o1 = (await _classes(app_client))["O1"]
    sec_a, sec_b = (s["id"] for s in o1["sections"])
    physics = next(s["course_id"] for s in o1["subjects"] if s["title"] == "Physics")
    s1, s2 = await _pupil(app_client, 1), await _pupil(app_client, 2)

    cands = (await app_client.get(f"{API}/sections/{sec_a}/candidates", params={"q": "pupil"})).json()
    assert {c["id"] for c in cands} >= {s1, s2}
    bad = await app_client.post(f"{API}/sections/{sec_a}/students", json={"student_ids": [s1], "optional_course_ids": [str(uuid.uuid4())]})
    assert bad.status_code == 422
    r = await app_client.post(f"{API}/sections/{sec_a}/students", json={"student_ids": [s1, s2], "optional_course_ids": [physics]})
    assert r.status_code == 200, r.text
    assert [p["subjects"] for p in r.json()["placed"]] == [6, 6] and r.json()["failed"] == []      # 5 compulsory + Physics
    sec = r.json()["section"]
    assert len(sec["students"]) == 2 and next(s for s in sec["subjects"] if s["title"] == "Physics")["students"] == 2
    assert next(s for s in sec["subjects"] if s["title"] == "Chemistry")["students"] == 0
    st = (await db_session.execute(select(Student).where(Student.id == uuid.UUID(s1)))).scalar_one()
    assert (st.section_label, st.current_semester) == ("A", 1)

    # moving to section B takes the subjects along (no classes held yet)
    moved = await app_client.post(f"{API}/sections/{sec_b}/students", json={"student_ids": [s1], "optional_course_ids": [physics]})
    assert moved.status_code == 200 and moved.json()["placed"][0]["subjects"] == 6
    rows = (await db_session.execute(select(CourseOffering.section_label).join(Enrollment, Enrollment.course_offering_id == CourseOffering.id)
                                     .where(Enrollment.student_id == uuid.UUID(s1), Enrollment.status == EnrollmentStatus.ENROLLED))).scalars().all()
    assert len(rows) == 6 and set(rows) == {"B"}
    overview = (await _classes(app_client))["O1"]
    assert [s["students"] for s in overview["sections"]] == [1, 1]

    # seats
    assert (await app_client.patch(f"{API}/sections/{sec_b}", json={"capacity": 1})).status_code == 200
    full = await app_client.post(f"{API}/sections/{sec_b}/students", json={"student_ids": [s2]})
    assert full.status_code == 422 and "1 seats" in full.text
    # a subject someone is enrolled in cannot be removed; an occupied section cannot be removed
    assert (await app_client.delete(f"{API}/classes/{o1['id']}/subjects/{physics}")).status_code == 422
    assert (await app_client.delete(f"{API}/sections/{sec_a}")).status_code == 422


async def test_school_words_in_menus_and_the_settings_entry(app_client, admin_user, monkeypatch):
    await _login_admin(app_client)
    page = (await app_client.get("/dashboard/admin/courses", headers={"accept": "text/html"})).text
    assert "Supplementary Exams" in page and "/dashboard/admin/classes" not in page                     # college / university
    monkeypatch.setattr(settings, "school_mode", 1)
    page = (await app_client.get("/dashboard/admin/courses", headers={"accept": "text/html"})).text
    assert "Supplementary Exams" not in page and 'href="/dashboard/admin/classes"' in page and "Classes &amp; sections" in page
    assert "<span>Subjects</span>" in page and "<span>Courses</span>" not in page
    classes = await app_client.get("/dashboard/admin/classes", headers={"accept": "text/html"})
    assert classes.status_code == 200 and "school-classes.js" in classes.text
    pol = (await app_client.get("/api/v1/policy")).json()
    keys = {p["key"] for p in (pol if isinstance(pol, list) else pol.get("policies", pol.get("items", [])))}
    assert "school_mode" in keys
