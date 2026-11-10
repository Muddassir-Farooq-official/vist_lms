"""Exam duty: who is free, how many are needed, one in charge, teachers told; the exam roll (separate from class
attendance, only eligible students, barred ones apart); absent students in marks entry; unfair means cases."""
from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import select, update

from app.academics.models import AssessmentType, ClassSession, ClassSessionStatus, CourseOffering, OfferingAssessment
from app.core.clock import institute_now, institute_today
from app.core.config import settings
from app.employees.models import Employee
from app.notifications.models import Notification
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _create_n_sessions, _login, _login_admin, _setup_offering_with_enrolled_students

pytestmark = pytest.mark.asyncio
E = "/api/v1/exams"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _current(db, *offering_ids, teacher=None):
    """Make the offerings' term run from a month ago to two months ahead (the test data's term is in the past)."""
    values = {"term_start_date": institute_today() - timedelta(days=30), "term_end_date": institute_today() + timedelta(days=60)}
    for oid in offering_ids:
        await db.execute(update(CourseOffering).where(CourseOffering.id == uuid.UUID(oid)).values(**values))
    await db.commit()


async def _emp(db, code):
    return (await db.execute(select(Employee).where(Employee.first_name == code))).scalar_one()


async def _titles(db, user_id):
    return [n.title for n in (await db.execute(select(Notification).where(Notification.user_id == user_id))).scalars()]


async def _schedule(client, offering_id, name="Midterm"):
    term = (await client.get(f"/api/v1/academics/offerings/{offering_id}")).json()["term_label"]
    r = await client.post(f"{E}/schedules", json={"term_label": term, "name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _add(client, sid, oid, day, start="09:00", end="12:00", room="Hall A"):
    r = await client.post(f"{E}/schedules/{sid}/papers", json={"course_offering_id": oid, "exam_date": day.isoformat(), "start_time": start,
                                                               "end_time": end, "room": room})
    assert r.status_code == 201, r.text
    return next(p for p in r.json()["papers"] if p["course_offering_id"] == oid)


async def test_free_teachers_are_suggested_and_duties_are_told(app_client, admin_user, db_session, monkeypatch):
    monkeypatch.setattr(settings, "exam_students_per_invigilator", 2)
    await _login_admin(app_client)
    o1, _s1 = await _setup_offering_with_enrolled_students(app_client, code="DT1", n_students=3, cnic_base=5400)
    o2, _s2 = await _setup_offering_with_enrolled_students(app_client, code="DT2", n_students=1, cnic_base=5410)
    await _current(db_session, o1, o2)
    await _login_admin(app_client)
    names = {}
    for code, cnic in (("DTA", "35202-5420001-1"), ("DTB", "35202-5430001-1"), ("DTC", "35202-5440001-1")):
        names[code] = await _staff(app_client, db_session, code, "TEACHER", cnic)
    a, b, c = [await _emp(db_session, x) for x in ("DTA", "DTB", "DTC")]
    day = institute_today() + timedelta(days=3)

    # A teaches a class at 10:00 that day
    await db_session.execute(update(CourseOffering).where(CourseOffering.id == uuid.UUID(o2)).values(assigned_teacher_id=a.id))
    db_session.add(ClassSession(course_offering_id=uuid.UUID(o2), session_date=day, start_time=time(10), end_time=time(11), room="R-1",
                                status=ClassSessionStatus.SCHEDULED))
    await db_session.commit()

    sid = await _schedule(app_client, o1)
    p1 = await _add(app_client, sid, o1, day)
    p2 = await _add(app_client, sid, o2, day, room="Hall B")
    assert (await app_client.put(f"{E}/papers/{p2['id']}/duty", json={"employee_ids": [str(b.id)]})).status_code == 200     # B: on duty in Hall B

    plan = (await app_client.get(f"{E}/papers/{p1['id']}/duty")).json()
    assert plan["students"] == 3 and plan["needed"] == 2 and plan["assigned"] == []
    people = {t["employee_id"]: t for t in plan["teachers"]}
    assert people[str(a.id)]["free"] is False and people[str(a.id)]["busy_reason"].startswith("Teaching DT2")
    assert people[str(b.id)]["free"] is False and people[str(b.id)]["busy_reason"].startswith("On duty: DT2")
    assert people[str(c.id)]["free"] is True and str(c.id) in plan["suggested"]
    assert str(a.id) not in plan["suggested"] and str(b.id) not in plan["suggested"]
    assert plan["teachers"][0]["free"] is True                                            # free teachers first

    clash = await app_client.put(f"{E}/papers/{p1['id']}/duty", json={"employee_ids": [str(b.id)]})
    assert clash.status_code == 422 and "Already on duty" in clash.text
    ok = await app_client.put(f"{E}/papers/{p1['id']}/duty", json={"employee_ids": [str(a.id), str(c.id)], "in_charge_id": str(c.id)})
    assert ok.status_code == 200, ok.text                                                 # A is busy teaching, chosen on purpose
    assert [(x["employee_id"], x["role"]) for x in ok.json()["assigned"]] == [(str(c.id), "IN_CHARGE"), (str(a.id), "ASSISTANT")]
    detail = (await app_client.get(f"{E}/schedules/{sid}")).json()
    row = next(p for p in detail["papers"] if p["id"] == p1["id"])
    assert row["invigilator_employee_id"] == str(c.id) and len(row["on_duty"]) == 2 and row["needed"] == 2

    # a draft tells nobody; publishing tells each teacher once
    assert not [t for t in await _titles(db_session, c.user_id) if "exam duties" in t]
    assert (await app_client.post(f"{E}/schedules/{sid}/publish")).status_code == 200
    assert (await _titles(db_session, c.user_id)).count("Your Midterm exam duties") == 1
    assert (await _titles(db_session, b.user_id)).count("Your Midterm exam duties") == 1

    # after publishing: each change is told
    assert (await app_client.put(f"{E}/papers/{p1['id']}/duty", json={"employee_ids": [str(c.id)]})).status_code == 200
    assert any(t.startswith("Exam duty removed: DT1") for t in await _titles(db_session, a.user_id))
    assert (await app_client.patch(f"{E}/papers/{p1['id']}", json={"room": "Hall C"})).status_code == 200
    assert any(t.startswith("Exam duty changed: DT1") for t in await _titles(db_session, c.user_id))
    from app.sms.models import SmsMessage

    texts = (await db_session.execute(select(SmsMessage).where(SmsMessage.event == "EXAM_DUTY"))).scalars().all()
    assert texts and all(m.recipient == "STAFF" for m in texts)

    # the teacher sees their duty
    await _first_login(app_client, *names["DTC"])
    mine = (await app_client.get(f"{E}/teaching/duties")).json()
    assert [(d["label"], d["role"], d["room"]) for d in mine] == [("DT1-C1", "IN_CHARGE", "Hall C")]
    page = await app_client.get("/dashboard/faculty/exams", headers={"accept": "text/html"})
    assert "exam-roll.js" in page.text and 'id="my-duties"' in page.text
    assert (await app_client.get(f"{E}/papers/{p1['id']}/duty")).status_code == 403                # only the exam office plans duty


async def test_exam_roll_only_eligible_students_marked_by_the_teacher_on_duty(app_client, admin_user, db_session, monkeypatch):
    await _login_admin(app_client)
    o1, studs = await _setup_offering_with_enrolled_students(app_client, code="ER1", n_students=3, cnic_base=5450)
    s_ok1, s_ok2, s_short = (uuid.UUID(s[0]) for s in studs)
    for sess in await _create_n_sessions(app_client, o1, 3):                # the third student misses every class: not allowed in
        r = await app_client.post(f"/api/v1/attendance/sessions/{sess}/mark", json={"present_student_ids": [str(s_ok1), str(s_ok2)], "late_note": "t"})
        assert r.status_code in (200, 201), r.text
    await _current(db_session, o1)
    await _login_admin(app_client)
    t_user, t_first = await _staff(app_client, db_session, "ERT", "TEACHER", "35202-5460001-1")
    o_user, o_first = await _staff(app_client, db_session, "ERO", "TEACHER", "35202-5470001-1")
    teacher = await _emp(db_session, "ERT")
    await db_session.execute(update(CourseOffering).where(CourseOffering.id == uuid.UUID(o1)).values(assigned_teacher_id=teacher.id))
    at = AssessmentType(name="Mid Term", max_marks=50)
    db_session.add(at)
    await db_session.flush()
    oa = OfferingAssessment(course_offering_id=uuid.UUID(o1), assessment_type_id=at.id)
    db_session.add(oa)
    await db_session.commit()

    sid = await _schedule(app_client, o1)
    paper = await _add(app_client, sid, o1, institute_today(), "08:00", "09:00")
    assert (await app_client.get(f"{E}/papers/{paper['id']}/roll")).json()["can_mark"] is False      # not published yet
    await app_client.put(f"{E}/papers/{paper['id']}/duty", json={"employee_ids": [str(teacher.id)]})
    assert (await app_client.post(f"{E}/schedules/{sid}/publish")).status_code == 200

    from app.attendance.service import AttendanceService

    before = await AttendanceService(db_session).compute_percentage(s_ok2, uuid.UUID(o1))

    t_pw = await _first_login(app_client, t_user, t_first)
    roll = (await app_client.get(f"{E}/papers/{paper['id']}/roll")).json()
    assert roll["can_mark"] is True and {r["student_id"] for r in roll["students"]} == {str(s_ok1), str(s_ok2)}
    assert [b["student_id"] for b in roll["barred"]] == [str(s_short)] and "%" in roll["barred"][0]["reasons"][0]
    refused = await app_client.put(f"{E}/papers/{paper['id']}/roll", json={"entries": [{"student_id": str(s_short), "status": "PRESENT"}]})
    assert refused.status_code == 422 and "not allowed" in refused.text
    done = await app_client.put(f"{E}/papers/{paper['id']}/roll", json={"entries": [{"student_id": str(s_ok1), "status": "LATE"},
                                                                                   {"student_id": str(s_ok2), "status": "ABSENT"}]})
    assert done.status_code == 200 and done.json()["counts"] == {"PRESENT": 0, "ABSENT": 1, "LATE": 1, "unmarked": 0}

    # class attendance is untouched
    after = await AttendanceService(db_session).compute_percentage(s_ok2, uuid.UUID(o1))
    assert after == before

    # the absent student shows in marks entry under the matching result type ("Midterm" = "Mid Term")
    absent = (await app_client.get(f"{E}/offerings/{o1}/absences")).json()
    assert [(a["student_id"], a["offering_assessment_id"], a["schedule"]) for a in absent] == [(str(s_ok2), str(oa.id), "Midterm")]
    assert "/api/v1/exams/offerings/" in (await app_client.get("/static/js/faculty-grading.js")).text

    # an unfair means case goes to the exam office
    case = await app_client.post(f"{E}/papers/{paper['id']}/cases", json={"student_id": str(s_ok1), "note": "Notes found under the answer sheet."})
    assert case.status_code == 201 and case.json()["status"] == "OPEN"
    assert any(t == "Unfair means case: ER1-C1" for t in await _titles(db_session, admin_user.id))

    # the next day the teacher can no longer change it; the exam office still can
    tomorrow = institute_now() + timedelta(days=1)
    monkeypatch.setattr("app.exams.duty.institute_now", lambda: tomorrow)
    late = await app_client.put(f"{E}/papers/{paper['id']}/roll", json={"entries": [{"student_id": str(s_ok2), "status": "PRESENT"}]})
    assert late.status_code == 422 and "exam day" in late.text

    # a teacher not on duty cannot open the roll
    await _first_login(app_client, o_user, o_first)
    assert (await app_client.get(f"{E}/papers/{paper['id']}/roll")).status_code == 403
    assert (await app_client.get(f"{E}/offerings/{o1}/absences")).status_code == 403

    await _login_admin(app_client)
    fix = await app_client.put(f"{E}/papers/{paper['id']}/roll", json={"entries": [{"student_id": str(s_ok2), "status": "PRESENT"}]})
    assert fix.status_code == 200 and fix.json()["counts"]["ABSENT"] == 0
    cases = (await app_client.get(f"{E}/cases", params={"status": "OPEN"})).json()
    assert [c["note"] for c in cases] == ["Notes found under the answer sheet."]
    assert (await app_client.post(f"{E}/cases/{cases[0]['id']}/decide", json={"decision": "x"})).status_code == 422
    decided = await app_client.post(f"{E}/cases/{cases[0]['id']}/decide", json={"decision": "Paper cancelled; warning issued."})
    assert decided.status_code == 200 and decided.json()["status"] == "DECIDED"
    assert (await app_client.post(f"{E}/cases/{cases[0]['id']}/decide", json={"decision": "Again and again"})).status_code in (400, 409, 422)
    assert (await app_client.get(f"{E}/schedules/{sid}")).json()["cases"][0]["decision"] == "Paper cancelled; warning issued."
    page = await app_client.get("/dashboard/admin/datesheets", headers={"accept": "text/html"})
    assert "exam-roll.js" in page.text and 'id="dt-box"' in page.text and "exam-duty.css" in page.text
    _ = t_pw, datetime


async def test_old_invigilator_field_still_works_and_settings_exist(app_client, admin_user, db_session):
    await _login_admin(app_client)
    o1, _s = await _setup_offering_with_enrolled_students(app_client, code="EO1", n_students=1, cnic_base=5480)
    await _login_admin(app_client)
    await _staff(app_client, db_session, "EOT", "TEACHER", "35202-5490001-1")
    t = await _emp(db_session, "EOT")
    sid = await _schedule(app_client, o1)
    term = (await app_client.get(f"/api/v1/academics/offerings/{o1}")).json()
    r = await app_client.post(f"{E}/schedules/{sid}/papers", json={"course_offering_id": o1, "exam_date": term["term_start_date"], "start_time": "09:00",
                                                                   "end_time": "10:00", "room": "Hall A", "invigilator_employee_id": str(t.id)})
    assert r.status_code == 201, r.text
    assert r.json()["papers"][0]["on_duty"][0]["role"] == "IN_CHARGE"
    pid = r.json()["papers"][0]["id"]
    cleared = (await app_client.patch(f"{E}/papers/{pid}", json={"invigilator_employee_id": None})).json()
    assert cleared["papers"][0]["on_duty"] == [] and cleared["papers"][0]["invigilator_employee_id"] is None
    pol = (await app_client.get("/api/v1/policy")).json()
    keys = {p["key"] for p in (pol if isinstance(pol, list) else pol.get("policies", pol.get("items", [])))}
    assert {"exam_students_per_invigilator", "sms_on_exam_duty"} <= keys
