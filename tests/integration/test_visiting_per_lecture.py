"""Visiting faculty are paid per lecture they take (1.66.0): taken = they marked its attendance; a lecture not taken,
covered by another teacher, or marked by the office is shown on the payslip as a deduction with its dates; cancelled
classes do not count; days absent and late marks do not apply; a cover of their class is worth one of their lectures."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.academics.models import ClassSession, ClassSessionStatus
from app.attendance.models import AttendanceRecord, AttendanceStatus
from app.employees.models import Employee
from app.teaching.models import ClassCover
from tests.integration.test_smart_timetable import _q as _q  # noqa: F401  (rate limits off)
from tests.integration.test_attendance_and_grading import _student_payload
from tests.integration.test_smart_timetable import A, TODAY, _setup

pytestmark = pytest.mark.asyncio
P = "/api/v1/payroll"
RATE = 1500


async def _visiting_month(client, db):
    """STA teaches SM101 A every weekday since three weeks ago; returns (sta, stb, month, past classes of that month)."""
    offs, teachers, _l, periods = await _setup(client, db)
    for day in ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"):
        r = await client.post(f"{A}/offerings/{offs['101A']}/timetable-slots", json={"day_of_week": day, "period_id": periods[0], "room": "SR1"})
        assert r.status_code == 201, r.text
    from tests.integration.test_attendance_and_grading import _login_admin

    sid = (await client.post(f"{A.replace('/academics', '')}/students", json=_student_payload(cnic="35202-7710001-1"))).json()["id"]
    db.info["student"] = sid
    await _login_admin(client)
    sta = await db.get(Employee, teachers["STA"])
    stb = await db.get(Employee, teachers["STB"])
    past = list((await db.execute(select(ClassSession).where(ClassSession.course_offering_id == offs["101A"], ClassSession.session_date < TODAY)
                                  .order_by(ClassSession.session_date))).scalars())
    for later in (await db.execute(select(ClassSession).where(ClassSession.course_offering_id == offs["101A"], ClassSession.session_date >= TODAY))).scalars():
        later.status = ClassSessionStatus.CANCELLED        # only finished days count, whatever the time of day the test runs
    await db.commit()
    month = Counter(s.session_date.replace(day=1) for s in past).most_common(1)[0][0]
    return sta, stb, month, [s for s in past if s.session_date.replace(day=1) == month]


async def test_lectures_are_paid_when_taken_and_the_rest_are_shown_as_deducted(app_client, admin_user, db_session):
    sta, stb, month, classes = await _visiting_month(app_client, db_session)
    assert len(classes) >= 5

    # the rate is only for visiting faculty
    r = await app_client.put(f"{P}/visiting/{sta.id}/rate", json={"lecture_rate": RATE})
    assert r.status_code == 422 and "visiting" in r.text.lower()
    sta.employment_type = "VISITING"
    await db_session.commit()
    assert (await app_client.put(f"{P}/visiting/{sta.id}/rate", json={"lecture_rate": RATE})).status_code == 200

    now = datetime.now(timezone.utc)
    taken, missed, covered, office, cancelled = classes[0], classes[1], classes[2], classes[3], classes[4]
    taken.attendance_taken_at = now
    db_session.add(AttendanceRecord(class_session_id=taken.id, student_id=(await _a_student(db_session, taken)), status=AttendanceStatus.PRESENT,
                                    marked_by=sta.user_id, marked_at=now))
    covered.attendance_taken_at = now
    db_session.add(ClassCover(class_session_id=covered.id, cover_date=covered.session_date, absent_employee_id=sta.id, substitute_employee_id=stb.id,
                              description="SM101 cover", amount=RATE, status="ACTIVE"))
    office.attendance_taken_at, office.late_marking_note = now, "Teacher forgot"
    cancelled.status = ClassSessionStatus.CANCELLED
    await db_session.commit()
    rest = [c for c in classes[5:]]
    for c in rest:                                   # everything else this month: taken
        c.attendance_taken_at = now
        db_session.add(AttendanceRecord(class_session_id=c.id, student_id=(await _a_student(db_session, c)), status=AttendanceStatus.PRESENT,
                                        marked_by=sta.user_id, marked_at=now))
    await db_session.commit()

    sheet = (await app_client.get(f"{P}/visiting/{sta.id}/lectures", params={"month": str(month)})).json()
    states = {x["class_session_id"]: x["state"] for x in sheet["lectures"]}
    assert states[str(taken.id)] == "TAKEN" and states[str(missed.id)] == "NOT_TAKEN" and states[str(covered.id)] == "COVERED"
    assert states[str(office.id)] == "BY_OFFICE" and str(cancelled.id) not in states
    n_taken = 1 + len(rest)
    held = n_taken + 3
    assert sheet["count"]["TAKEN"] == n_taken and sheet["held"] == held
    assert sheet["earned"] == n_taken * RATE and sheet["not_paid"] == 3 * RATE
    assert (await app_client.get(f"{P}/visiting/{sta.id}/lectures.pdf", params={"month": str(month)})).status_code == 200
    ov = (await app_client.get(f"{P}/visiting", params={"month": str(month)})).json()["people"]
    assert [p for p in ov if p["employee_id"] == str(sta.id)][0]["rate"] == RATE

    # the payslip: every lecture held earned, the others deducted with dates; no monthly basic, no day-absence deduction
    run = await app_client.post(f"{P}/runs", json={"month": str(month)})
    assert run.status_code == 201, run.text
    slip_id = next(s for s in run.json()["payslips"] if s["employee_id"] == str(sta.id))["id"]
    slip = (await app_client.get(f"{P}/payslips/{slip_id}")).json()
    earn = {x["source"]: x for x in slip["earnings"]}
    ded = {x["source"]: x for x in slip["deduction_lines"]}
    assert "BASE" not in earn and earn["LECTURES"]["amount"] == held * RATE
    assert ded["LECTURE_MISSED"]["amount"] == RATE and f"{missed.session_date:%d %b}" in ded["LECTURE_MISSED"]["label"]
    assert ded["LECTURE_COVERED"]["amount"] == RATE and "STB" in ded["LECTURE_COVERED"]["label"]
    assert ded["LECTURE_OFFICE"]["amount"] == RATE
    assert not {"ABSENT", "LATE", "LATE_ABSENT"} & set(ded)
    assert slip["net"] == n_taken * RATE


async def test_a_cover_of_a_visiting_teachers_class_is_worth_one_lecture(app_client, admin_user, db_session):
    from app.teaching.service import TeachingService

    sta, _stb, _m, classes = await _visiting_month(app_client, db_session)
    sta.employment_type = "VISITING"
    await db_session.commit()
    assert (await app_client.put(f"{P}/visiting/{sta.id}/rate", json={"lecture_rate": RATE})).status_code == 200
    amount, how = await TeachingService(db_session).suggested_amount(sta.id, classes[0].session_date)
    assert amount == Decimal(RATE) and "lecture" in how
    # back to a monthly salary
    assert (await app_client.put(f"{P}/visiting/{sta.id}/rate", json={"lecture_rate": None})).json()["rate"] is None


async def _a_student(db, cs) -> object:
    """Attendance records need a student; any student will do for who marked the class."""
    import uuid

    return uuid.UUID(db.info["student"])


async def test_a_combined_class_is_one_lecture(app_client, admin_user, db_session):
    from tests.integration.test_combine_across_batches import OTHER, _twin_sections
    from tests.integration.test_smart_timetable import TERM

    _offs, teachers, _l, periods = await _setup(app_client, db_session)
    ids = await _twin_sections(app_client, teachers["STC"])
    assert (await app_client.post(f"{A}/offerings/{ids[TERM]}/combine", json={"offering_ids": [ids[OTHER]]})).status_code == 200
    assert (await app_client.post(f"{A}/offerings/{ids[TERM]}/timetable-slots", json={"day_of_week": "MONDAY", "period_id": periods[1], "room": "SR2"})).status_code == 201
    stc = await db_session.get(Employee, teachers["STC"])
    stc.employment_type = "VISITING"
    await db_session.commit()
    assert (await app_client.put(f"{P}/visiting/{stc.id}/rate", json={"lecture_rate": RATE})).status_code == 200
    past = list((await db_session.execute(select(ClassSession).where(ClassSession.course_offering_id.in_(list(ids.values())),
                                                                     ClassSession.session_date < TODAY))).scalars())
    day = min(s.session_date for s in past)
    both = [s for s in past if s.session_date == day]
    assert len(both) == 2                                          # one class in each batch
    sheet = (await app_client.get(f"{P}/visiting/{stc.id}/lectures", params={"month": str(day.replace(day=1))})).json()
    that_day = [x for x in sheet["lectures"] if x["date"] == str(day)]
    assert len(that_day) == 1 and len(that_day[0]["sections"]) == 2
