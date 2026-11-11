"""Combined classes: AHS201 taught to BS Anesthesia (section A) and BS Dental (section B) together by one teacher,
at the same time, in the same room. One timetable, one attendance list, one marks scheme, one exam paper;
each section keeps its own students, attendance and marks."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.academics.models import AssessmentType, ClassSession, CourseOffering, OfferingAssessment, TimetableSlot
from app.attendance.models import AttendanceRecord
from app.core.config import settings
from app.employees.models import Employee
from tests.integration.test_announcements import _staff
from tests.integration.test_attendance_and_grading import (SLOT_DAY_OF_WEEK, TERM_END, TERM_START, _create_and_activate_student,
                                                         _get_or_create_period, _login, _login_admin)

pytestmark = pytest.mark.asyncio
A = "/api/v1/academics"


@pytest.fixture(autouse=True)
def _q(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _two_programs_one_course(client, db):
    """BS Anesthesia (2 students in section A) and BS Dental (1 student in section B) share AHS201."""
    await _login_admin(client)
    progs = []
    for code, name in (("ANS", "BS Anesthesia"), ("DEN", "BS Dental")):
        r = await client.post("/api/v1/programs", json={"code": code, "name": name, "duration_years": 4, "total_semesters": 8})
        progs.append(r.json()["id"])
    course = (await client.post(f"{A}/courses", json={"code": "AHS201", "title": "Human Anatomy", "credit_hours": 3})).json()["id"]
    for p in progs:
        assert (await client.post(f"{A}/courses/{course}/programs", json={"program_id": p, "semester_number": 1})).status_code in (200, 201)
    offs = []
    for label in ("A", "B"):
        r = await client.post(f"{A}/offerings", json={"course_id": course, "term_label": "Fall 2026", "section_label": label,
                                                     "term_start_date": str(TERM_START), "term_end_date": str(TERM_END)})
        assert r.status_code == 201, r.text
        offs.append(r.json()["id"])
        await client.patch(f"{A}/offerings/{offs[-1]}", json={"is_open_for_enrollment": True})
    t1, _ = await _staff(client, db, "CMT1", "TEACHER", "35202-7400001-1")
    t2, _ = await _staff(client, db, "CMT2", "TEACHER", "35202-7410001-1")
    e1 = (await db.execute(select(Employee).where(Employee.first_name == "CMT1"))).scalar_one()
    e2 = (await db.execute(select(Employee).where(Employee.first_name == "CMT2"))).scalar_one()
    await client.patch(f"{A}/offerings/{offs[0]}", json={"assigned_teacher_id": str(e1.id)})
    await client.patch(f"{A}/offerings/{offs[1]}", json={"assigned_teacher_id": str(e2.id)})
    students = []
    for i, (prog, off) in enumerate(((progs[0], offs[0]), (progs[0], offs[0]), (progs[1], offs[1]))):
        sid, username = await _create_and_activate_student(client, cnic=f"74{i + 2}20-0000001-1", program_id=prog, current_semester=1)
        await _login(client, username, "StudentPass123!")
        assert (await client.post(f"{A}/enrollments/me", json={"course_offering_id": off})).status_code == 201
        students.append(sid)
    await _login_admin(client)
    return offs, students, (e1, e2)


async def test_combining_shares_the_teacher_the_timetable_and_the_marks_scheme(app_client, admin_user, db_session):
    (oa, ob), _studs, (t1, t2) = await _two_programs_one_course(app_client, db_session)
    t1_id, t2_id = t1.id, t2.id
    p9 = await _get_or_create_period(app_client, "09:00:00", "10:00:00")
    assert (await app_client.post(f"{A}/offerings/{oa}/timetable-slots",
                                  json={"day_of_week": SLOT_DAY_OF_WEEK, "period_id": p9, "room": "Hall 1"})).status_code == 201
    # not combined yet: the same room at the same time is a clash
    clash = await app_client.post(f"{A}/offerings/{ob}/timetable-slots", json={"day_of_week": SLOT_DAY_OF_WEEK, "period_id": p9, "room": "Hall 1"})
    assert clash.status_code == 422 and "already booked" in clash.text

    view = (await app_client.get(f"{A}/offerings/{oa}/combined")).json()
    assert view["combined"] is False and [m["section_label"] for m in view["can_join"]] == ["B"]
    assert view["can_join"][0]["programs"] == ["BS Dental"]
    two = await app_client.post(f"{A}/offerings/{oa}/combine", json={"offering_ids": [ob]})
    assert two.status_code == 422 and "different teachers" in two.text
    ok = await app_client.post(f"{A}/offerings/{oa}/combine", json={"offering_ids": [ob], "teacher_id": str(t1.id)})
    assert ok.status_code == 200, ok.text
    assert ok.json()["combined"] is True and ok.json()["students"] == 3 and [m["section_label"] for m in ok.json()["members"]] == ["A", "B"]

    b = (await db_session.execute(select(CourseOffering).where(CourseOffering.id == uuid.UUID(ob)))).scalar_one()
    await db_session.refresh(b)
    assert b.assigned_teacher_id == t1_id
    b_slots = (await db_session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id == b.id))).scalars().all()
    assert len(b_slots) == 1 and b_slots[0].room == "Hall 1"                                      # B got A's timetable
    assert (await app_client.get(f"{A}/offerings/{oa}")).json()["combined_with"] == ["B"]

    # a new slot on one section is made on the other too; removing it on the other removes both
    p11 = await _get_or_create_period(app_client, "11:00:00", "12:00:00")
    assert (await app_client.post(f"{A}/offerings/{oa}/timetable-slots",
                                  json={"day_of_week": SLOT_DAY_OF_WEEK, "period_id": p11, "room": "Hall 1"})).status_code == 201
    b_slots = (await db_session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id == uuid.UUID(ob)))).scalars().all()
    assert len(b_slots) == 2
    eleven = next(s for s in b_slots if s.start_time.hour == 11)
    assert (await app_client.delete(f"{A}/timetable-slots/{eleven.id}")).status_code in (200, 204)
    db_session.expire_all()
    a_slots = (await db_session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id == uuid.UUID(oa)))).scalars().all()
    assert [s.start_time.hour for s in a_slots] == [9]

    # changing the teacher changes it for both
    assert (await app_client.patch(f"{A}/offerings/{ob}", json={"assigned_teacher_id": str(t2_id)})).status_code == 200
    db_session.expire_all()
    assert {o.assigned_teacher_id for o in (await db_session.execute(select(CourseOffering).where(
        CourseOffering.id.in_([uuid.UUID(oa), uuid.UUID(ob)])))).scalars()} == {t2_id}

    # one marks scheme
    at = AssessmentType(name="Quiz CMB", max_marks=10)
    db_session.add(at)
    await db_session.commit()
    assert (await app_client.post(f"{A}/offerings/{oa}/assessments", json={"assessment_type_id": str(at.id)})).status_code == 201
    at_id = at.id
    in_b = (await db_session.execute(select(OfferingAssessment).where(OfferingAssessment.course_offering_id == uuid.UUID(ob)))).scalars().all()
    assert [x.assessment_type_id for x in in_b] == [at_id]

    # take B out again
    sep = await app_client.post(f"{A}/offerings/{ob}/separate")
    assert sep.status_code == 200 and sep.json()["combined"] is False
    assert (await app_client.get(f"{A}/offerings/{oa}")).json()["combined_with"] == []


async def test_one_attendance_list_saved_to_each_section_and_one_exam_paper(app_client, admin_user, db_session):
    (oa, ob), (s1, s2, s3), (t1, _t2) = await _two_programs_one_course(app_client, db_session)
    p9 = await _get_or_create_period(app_client, "09:00:00", "10:00:00")
    await app_client.post(f"{A}/offerings/{oa}/timetable-slots", json={"day_of_week": SLOT_DAY_OF_WEEK, "period_id": p9, "room": "Hall 1"})
    assert (await app_client.post(f"{A}/offerings/{oa}/combine", json={"offering_ids": [ob], "teacher_id": str(t1.id)})).status_code == 200

    roster = (await app_client.get(f"{A}/offerings/{oa}/roster", params={"combined": "true"})).json()
    assert sorted((r["program"], r["section_label"]) for r in roster) == [("BS Anesthesia", "A"), ("BS Anesthesia", "A"), ("BS Dental", "B")]
    assert len((await app_client.get(f"{A}/offerings/{oa}/roster")).json()) == 2                 # without the flag: unchanged

    first = sorted((await app_client.get(f"{A}/offerings/{oa}/class-sessions")).json(), key=lambda s: s["session_date"])[0]
    r = await app_client.post(f"/api/v1/attendance/sessions/{first['id']}/mark",
                              json={"present_student_ids": [s1, s3], "late_note": "Combined class, marked by the office", "combined": True})
    assert r.status_code == 200, r.text
    assert len(r.json()) == 3
    twin = (await db_session.execute(select(ClassSession).where(ClassSession.course_offering_id == uuid.UUID(ob),
                                                                ClassSession.session_date == date.fromisoformat(first["session_date"])))).scalar_one()
    recs = {str(x.student_id): (x.class_session_id, x.status.value) for x in (await db_session.execute(select(AttendanceRecord))).scalars()}
    assert recs[s3] == (twin.id, "PRESENT") and recs[s1][1] == "PRESENT" and recs[s2][1] == "ABSENT"
    assert str(recs[s1][0]) == first["id"]                                                       # each saved to the student's own section
    sheet = (await app_client.get(f"/api/v1/attendance/sessions/{first['id']}", params={"combined": "true"})).json()
    assert len(sheet) == 3
    pct = (await app_client.get(f"/api/v1/attendance/offerings/{ob}/students/{s3}/percentage")).json()
    assert pct["present_count"] == 1

    # one exam paper for the combined class
    sched = (await app_client.post("/api/v1/exams/schedules", json={"term_label": "Fall 2026", "name": "Midterm"})).json()["id"]
    day = (TERM_START + timedelta(days=30)).isoformat()
    paper = await app_client.post(f"/api/v1/exams/schedules/{sched}/papers", json={"course_offering_id": oa, "exam_date": day, "start_time": "09:00",
                                                                                   "end_time": "11:00", "room": "Exam Hall"})
    assert paper.status_code == 201, paper.text
    second = await app_client.post(f"/api/v1/exams/schedules/{sched}/papers", json={"course_offering_id": ob, "exam_date": day, "start_time": "09:00",
                                                                                    "end_time": "11:00", "room": "Exam Hall 2"})
    assert second.status_code == 409 and "combined class" in second.text
    pid = paper.json()["papers"][0]["id"]
    plan = (await app_client.get(f"/api/v1/exams/papers/{pid}/duty")).json()
    assert plan["students"] == 3                                                                 # both sections sit it
    roll = (await app_client.get(f"/api/v1/exams/papers/{pid}/roll")).json()
    assert len(roll["students"]) + len(roll["barred"]) == 3


async def test_pages_know_about_combined_classes(app_client, admin_user):
    await _login_admin(app_client)
    js = (await app_client.get("/static/js/faculty-attendance.js")).text
    assert "combined=true" in js and "combined: isCombined()" in js
    assert "combined=true" in (await app_client.get("/static/js/faculty-grading.js")).text
    assert "/combine" in (await app_client.get("/static/js/combined-class.js")).text
