"""Handing over a member of staff's work (1.67.0): while away (suspended / on leave) each class gets a cover and the
covers still to come are taken back when they return; when they leave each running course gets a new teacher (its
timetable goes with it). Both: covers they were going to take are given back and exam duties move. Nothing held changes."""
from __future__ import annotations

from datetime import time, timedelta

import pytest
from sqlalchemy import select

from app.academics.models import ClassSession, CourseOffering
from app.employees.models import Employee, EmployeeStatus
from app.exams.models import DutyRole, ExamDuty, ExamPaper, ExamSchedule
from app.teaching.models import ClassCover
from tests.integration.test_announcements import _first_login
from tests.integration.test_smart_timetable import _q as _q  # noqa: F401  (rate limits off)
from tests.integration.test_smart_timetable import A, TERM, TODAY, _setup

pytestmark = pytest.mark.asyncio
E = "/api/v1/employees"


async def _week_of_classes(client, db):
    offs, teachers, logins, periods = await _setup(client, db)
    for day in ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"):
        assert (await client.post(f"{A}/offerings/{offs['101A']}/timetable-slots", json={"day_of_week": day, "period_id": periods[0], "room": "SR1"})).status_code == 201
        assert (await client.post(f"{A}/offerings/{offs['102A']}/timetable-slots", json={"day_of_week": day, "period_id": periods[1], "room": "SR2"})).status_code == 201
    return offs, teachers, logins, periods


async def test_away_each_class_gets_a_cover_and_they_come_back(app_client, admin_user, db_session):
    offs, t, _l, _p = await _week_of_classes(app_client, db_session)
    sta, stb, stc = t["STA"], t["STB"], t["STC"]
    start, until = TODAY + timedelta(days=1), TODAY + timedelta(days=14)
    # STA was going to cover one of STB's classes
    stb_class = (await db_session.execute(select(ClassSession).where(ClassSession.course_offering_id == offs["102A"],
                                                                      ClassSession.session_date >= start).order_by(ClassSession.session_date))).scalars().first()
    assert (await app_client.post(f"/api/v1/teaching/classes/{stb_class.id}/cover", json={"substitute_id": sta})).status_code == 201

    plan = (await app_client.get(f"{E}/{sta}/handover", params={"kind": "AWAY", "from_date": str(start), "until_date": str(until)})).json()
    assert plan["classes"] and all(start.isoformat() <= c["date"] <= until.isoformat() for c in plan["classes"])
    assert {c["course"] for c in plan["classes"]} == {"SM101"}
    assert len(plan["covers_taking"]) == 1

    body = {"kind": "AWAY", "from_date": str(start), "until_date": str(until),
            "classes": [{"class_session_id": c["class_session_id"], "substitute_id": stc} for c in plan["classes"]]}
    r = await app_client.post(f"{E}/{sta}/handover", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["done"]["covers"] == len(plan["classes"]) and r.json()["done"]["released"] == 1
    covers = list((await db_session.execute(select(ClassCover).where(ClassCover.absent_employee_id == sta, ClassCover.status == "ACTIVE"))).scalars())
    assert len(covers) == len(plan["classes"]) and {str(c.substitute_employee_id) for c in covers} == {stc}
    # the course is still theirs
    assert str((await db_session.get(CourseOffering, offs["101A"])).assigned_teacher_id) == sta

    # back at work: covers still to come are taken back
    assert (await app_client.post(f"{E}/{sta}/status", json={"new_status": "ON_LEAVE"})).status_code == 200
    assert (await app_client.post(f"{E}/{sta}/status", json={"new_status": "ACTIVE"})).status_code == 200
    db_session.expire_all()
    left = list((await db_session.execute(select(ClassCover).where(ClassCover.absent_employee_id == sta, ClassCover.status == "ACTIVE"))).scalars())
    assert left == []


async def test_leaving_each_course_gets_a_new_teacher_and_duties_move(app_client, admin_user, db_session):
    offs, t, _l, _p = await _week_of_classes(app_client, db_session)
    sta, stb, stc = t["STA"], t["STB"], t["STC"]
    sched = ExamSchedule(term_label=TERM, name="Midterm")
    db_session.add(sched)
    await db_session.flush()
    paper = ExamPaper(schedule_id=sched.id, course_offering_id=offs["101A"], exam_date=TODAY + timedelta(days=10), start_time=time(9), end_time=time(11),
                      room="SR1", invigilator_employee_id=sta)
    db_session.add(paper)
    await db_session.flush()
    db_session.add(ExamDuty(paper_id=paper.id, employee_id=sta, role=DutyRole.IN_CHARGE))
    await db_session.commit()
    paper_id = paper.id

    plan = (await app_client.get(f"{E}/{sta}/handover", params={"kind": "LEFT"})).json()
    codes = {(c["code"], c["section"]) for c in plan["courses"]}
    assert ("SM101", "A") in codes and ("SM101", "B") in codes
    assert len(plan["duties"]) == 1 and plan["duties"][0]["role"] == "IN_CHARGE"

    held_before = list((await db_session.execute(select(ClassSession.id).where(ClassSession.course_offering_id == offs["101A"],
                                                                                ClassSession.session_date < TODAY))).scalars())
    body = {"kind": "LEFT", "courses": [{"offering_id": offs["101A"], "teacher_id": stb}, {"offering_id": offs["101B"], "teacher_id": None}],
            "duties": [{"duty_id": plan["duties"][0]["duty_id"], "employee_id": stc}], "terminate": True, "reason": "Resigned"}
    r = await app_client.post(f"{E}/{sta}/handover", json=body)
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert str((await db_session.get(CourseOffering, offs["101A"])).assigned_teacher_id) == stb
    assert (await db_session.get(CourseOffering, offs["101B"])).assigned_teacher_id is None
    assert (await db_session.get(Employee, sta)).status == EmployeeStatus.TERMINATED
    inv = (await db_session.execute(select(ExamPaper.invigilator_employee_id).where(ExamPaper.id == paper_id))).scalar_one()
    assert str(inv) == stc
    # the timetable went with the course; classes already held are untouched
    assert (await app_client.get(f"{A}/offerings/{offs['101A']}/timetable-slots")).json()
    assert list((await db_session.execute(select(ClassSession.id).where(ClassSession.course_offering_id == offs["101A"],
                                                                         ClassSession.session_date < TODAY))).scalars()) == held_before
    # the course left without a teacher is still listed, to finish later
    again = (await app_client.get(f"{E}/{sta}/handover", params={"kind": "LEFT"})).json()
    rest = [c for c in again["courses"] if c["offering_id"] == offs["101B"]]
    assert rest and not rest[0]["still_theirs"]
    assert (await app_client.post(f"{E}/{sta}/handover", json={"kind": "LEFT", "courses": [{"offering_id": offs["101B"], "teacher_id": stc}]})).status_code == 200
    db_session.expire_all()
    assert str((await db_session.get(CourseOffering, offs["101B"])).assigned_teacher_id) == stc


async def test_only_the_office_hands_over(app_client, admin_user, db_session):
    _offs, t, logins, _p = await _week_of_classes(app_client, db_session)
    username, first = logins["STB"]
    await _first_login(app_client, username, first)
    assert (await app_client.get(f"{E}/{t['STA']}/handover", params={"kind": "AWAY"})).status_code == 403
    assert (await app_client.post(f"{E}/{t['STA']}/handover", json={"kind": "LEFT"})).status_code == 403
