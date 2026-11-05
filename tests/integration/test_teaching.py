"""Teachers' classes: missed attendance while on campus, punctuality, class cover (with cover pay), and changing the
timetable in the middle of a term without touching past classes or students' attendance."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.academics.models import ClassSession
from app.core.config import settings
from app.employees.models import JobRole
from app.notifications.models import Notification
from app.teaching.models import ClassCover
from app.teaching.service import MISSED_TITLE, TeachingService, covers_payable
from app.timekeeping.models import StaffAttendance
from tests.integration.test_attendance_and_grading import (
    SLOT_DAY_OF_WEEK,
    TERM_START,
    _create_n_sessions,
    _login,
    _login_admin,
    _setup_offering_with_enrolled_students,
)

pytestmark = pytest.mark.asyncio
API = "/api/v1/teaching"
D0 = TERM_START                       # the first class: D0 at 09:00-10:00


@pytest.fixture(autouse=True)
def _no_login_throttle(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(settings, "payroll_default_working_days", 26)


def _at(day: date, hh: int, mm: int = 0) -> datetime:
    """A wall-clock moment at the institute, stored the way the app stores moments (UTC)."""
    return datetime.combine(day, time(hh, mm), tzinfo=ZoneInfo(settings.institute_timezone)).astimezone(timezone.utc)


def _clock(monkeypatch, moment: datetime):
    """Pretend the institute's clock shows `moment` (naive wall-clock) everywhere these features read it."""
    import app.academics.service as acad
    import app.attendance.service as att
    import app.teaching.service as teach

    for mod in (acad, att, teach):
        if hasattr(mod, "institute_now"):
            monkeypatch.setattr(mod, "institute_now", lambda: moment)
        if hasattr(mod, "institute_today"):
            monkeypatch.setattr(mod, "institute_today", lambda: moment.date())


async def _employee(client, db_session, code, *, teacher=True, salary=26000, cnic="35202-9200001-1", department="Science"):
    jr = (await db_session.execute(select(JobRole).where(JobRole.code == ("TCH" if teacher else "OFF")))).scalars().first()
    if jr is None:
        jr = JobRole(code="TCH" if teacher else "OFF", title="Teacher" if teacher else "Office staff", default_role_code="TEACHER" if teacher else "STAFF")
        db_session.add(jr)
        await db_session.commit()
    r = await client.post("/api/v1/employees", json={
        "first_name": code, "last_name": "T", "date_of_birth": "1985-01-01", "gender": "Male", "cnic": cnic, "department": department,
        "contact_no": "03007770000", "hire_date": "2025-12-01", "job_role_id": str(jr.id), "base_salary": salary, "password": "TeacherPass123!"})
    assert r.status_code == 201, r.text
    return r.json()


async def _sign_in_employee(client, username):
    await _login(client, username, "TeacherPass123!")
    ch = await client.post("/api/v1/auth/change-password", json={"current_password": "TeacherPass123!", "new_password": "TeacherNew12345!"})
    assert ch.status_code == 204, ch.text
    await _login(client, username, "TeacherNew12345!")


async def _class_with_teacher(client, db_session, code, n_students=2):
    offering_id, studs = await _setup_offering_with_enrolled_students(client, code=code, n_students=n_students, cnic_base=int("3" + str(abs(hash(code)) % 900 + 100)))
    teacher = await _employee(client, db_session, f"{code}A", cnic=f"35202-{abs(hash(code)) % 9000000 + 1000000}-1")
    assert (await client.patch(f"/api/v1/academics/offerings/{offering_id}", json={"assigned_teacher_id": teacher["id"]})).status_code == 200
    sessions = await _create_n_sessions(client, offering_id, 3)
    return offering_id, studs, teacher, sessions


async def test_a_teacher_on_campus_who_misses_a_class_is_highlighted_and_the_administration_told_once(app_client, admin_user, db_session, monkeypatch):
    offering_id, _studs, teacher, sessions = await _class_with_teacher(app_client, db_session, "TM1")
    db_session.add(StaffAttendance(employee_id=teacher["id"], work_date=D0, check_in_at=_at(D0, 8, 5), is_working_day=True))
    await db_session.commit()
    _clock(monkeypatch, datetime.combine(D0, time(9, 40)))           # the 15-minute window closed at 09:15

    day = (await app_client.get(f"{API}/day", params={"on": str(D0)})).json()
    row = next(r for r in day["classes"] if r["class_session_id"] == sessions[0])
    assert row["state"] == "MISSED" and row["missed_while_in"] is True and row["presence"] == "IN" and row["needs_cover"] is False
    assert day["counts"]["missed_while_in"] == 1 and day["classes"][0]["class_session_id"] == sessions[0]      # shown first

    svc = TeachingService(db_session)
    assert await svc.alert_missed_classes() == 1
    assert await svc.alert_missed_classes() == 0                     # once per class, however often the sender runs
    notes = (await db_session.execute(select(Notification).where(Notification.title == MISSED_TITLE))).scalars().all()
    assert {n.user_id for n in notes} == {admin_user.id} and "did not take attendance" in notes[0].message and str(sessions[0]) in notes[0].link

    monkeypatch.setattr(settings, "missed_class_alerts", 0)          # Settings -> Rules -> Attendance can switch it off
    assert await TeachingService(db_session).alert_missed_classes() == 0


async def test_a_class_taken_in_time_or_not_yet_due_is_not_flagged(app_client, admin_user, db_session, monkeypatch):
    offering_id, studs, teacher, sessions = await _class_with_teacher(app_client, db_session, "TM2")
    db_session.add(StaffAttendance(employee_id=teacher["id"], work_date=D0, check_in_at=_at(D0, 8, 0), is_working_day=True))
    await db_session.commit()
    _clock(monkeypatch, datetime.combine(D0, time(9, 10)))           # still inside the window
    row = next(r for r in (await app_client.get(f"{API}/day", params={"on": str(D0)})).json()["classes"] if r["class_session_id"] == sessions[0])
    assert row["state"] == "OPEN" and not row["missed_while_in"]
    cs = await db_session.get(ClassSession, sessions[0])
    cs.attendance_taken_at = _at(D0, 9, 4)
    await db_session.commit()
    _clock(monkeypatch, datetime.combine(D0, time(11, 0)))
    row = next(r for r in (await app_client.get(f"{API}/day", params={"on": str(D0)})).json()["classes"] if r["class_session_id"] == sessions[0])
    assert row["state"] == "ON_TIME" and row["minutes_after_start"] == 4 and not row["missed_while_in"]
    assert await TeachingService(db_session).alert_missed_classes() == 0


async def test_punctuality_counts_on_time_late_and_missing_and_the_average_minutes(app_client, admin_user, db_session, monkeypatch):
    offering_id, studs, teacher, sessions = await _class_with_teacher(app_client, db_session, "TM3")
    s0, s1, s2 = [await db_session.get(ClassSession, s) for s in sessions]
    s0.attendance_taken_at = _at(s0.session_date, 9, 6)                                      # on time, 6 minutes in
    s1.attendance_taken_at, s1.late_marking_note = _at(s1.session_date, 13, 0), "Teacher was invigilating"   # by the office later
    await db_session.commit()                                                                # s2: never taken
    _clock(monkeypatch, datetime.combine(s2.session_date, time(12, 0)))
    r = await app_client.get(f"{API}/punctuality", params={"first": str(D0), "last": str(s2.session_date)})
    assert r.status_code == 200, r.text
    me = next(t for t in r.json()["teachers"] if t["employee_id"] == teacher["id"])
    assert (me["classes_due"], me["on_time"], me["late"], me["missed"]) == (3, 1, 1, 1)
    assert me["on_time_percent"] == 33.3 and me["average_minutes_after_start"] == 6.0 and me["verdict"] == "Often late or missing"
    csv = await app_client.get(f"{API}/punctuality.csv", params={"first": str(D0), "last": str(s2.session_date)})
    assert csv.status_code == 200 and "Taken on time" in csv.text and teacher["first_name"] in csv.text
    detail = (await app_client.get(f"{API}/teachers/{teacher['id']}/classes", params={"first": str(D0), "last": str(s2.session_date)})).json()
    assert [d["state"] for d in detail] == ["ON_TIME", "LATE", "MISSED"]


async def test_absent_teacher_cover_suggestion_assignment_marking_and_pay(app_client, admin_user, db_session, monkeypatch):
    offering_id, studs, absent, sessions = await _class_with_teacher(app_client, db_session, "TM4", n_students=2)
    free = await _employee(app_client, db_session, "Free", cnic="35202-9300001-1")
    office = await _employee(app_client, db_session, "Office", teacher=False, cnic="35202-9300002-1", department="Admin")
    db_session.add(StaffAttendance(employee_id=free["id"], work_date=D0, check_in_at=_at(D0, 7, 55), is_working_day=True))
    await db_session.commit()
    _clock(monkeypatch, datetime.combine(D0, time(8, 30)))

    day = (await app_client.get(f"{API}/day", params={"on": str(D0)})).json()
    row = next(r for r in day["classes"] if r["class_session_id"] == sessions[0])
    assert row["needs_cover"] is True and any(a["employee_id"] == absent["id"] for a in day["absent_teachers"])

    opts = (await app_client.get(f"{API}/classes/{sessions[0]}/cover-options")).json()
    ids = [p["employee_id"] for p in opts["people"]]
    assert absent["id"] not in ids and ids.index(free["id"]) < ids.index(office["id"])      # a teacher who checked in comes first
    best = next(p for p in opts["people"] if p["employee_id"] == free["id"])
    assert best["recommended"] and "same department (Science)" in best["reasons"]
    assert opts["suggested_amount"] == 1000.0 and "26 working days" in opts["amount_note"]   # 26,000 / 26 days, one class that day

    given = await app_client.post(f"{API}/classes/{sessions[0]}/cover", json={"substitute_id": free["id"]})
    assert given.status_code == 201, given.text
    cover_id = given.json()["id"]
    from app.employees.models import Employee

    free_user = (await db_session.get(Employee, __import__("uuid").UUID(free["id"]))).user_id
    told = (await db_session.execute(select(Notification).where(Notification.user_id == free_user, Notification.title == "You are covering a class"))).scalars().all()
    assert len(told) == 1 and "/tasks#covers" in told[0].link

    assert covers_payable is not None
    assert await covers_payable(db_session, free["id"], date(2026, 1, 31), run_id=__import__("uuid").uuid4()) == []   # not held yet: not paid

    # someone else cannot use it; the substitute takes attendance within the window like the class's own teacher
    await _sign_in_employee(app_client, office["username"])
    assert (await app_client.get(f"{API}/covers/me/{cover_id}/register")).status_code == 403
    assert (await app_client.get(f"{API}/day")).status_code == 403                          # the admin view is the administration's
    await _sign_in_employee(app_client, free["username"])
    _clock(monkeypatch, datetime.combine(D0, time(9, 5)))
    mine = (await app_client.get(f"{API}/covers/me")).json()
    assert [m["id"] for m in mine] == [cover_id] and mine[0]["can_mark"] is True
    reg = (await app_client.get(f"{API}/covers/me/{cover_id}/register")).json()
    assert len(reg["students"]) == 2
    marked = await app_client.post(f"{API}/covers/me/{cover_id}/mark", json={"present_student_ids": [studs[0][0]]})
    assert marked.status_code == 200, marked.text
    assert marked.json() == {"marked": 2, "present": 1}
    # not the substitute's own other class
    assert (await app_client.post(f"/api/v1/attendance/sessions/{sessions[1]}/mark", json={"present_student_ids": []})).status_code == 403

    # pay: on the substitute's January payslip, once
    await _login_admin(app_client)
    from app.payroll.models import PayslipLine
    from app.payroll.service import PayrollService

    run, _ = await PayrollService(db_session).prepare(date(2026, 1, 1), actor_id=admin_user.id)
    await db_session.commit()
    lines = (await db_session.execute(select(PayslipLine).where(PayslipLine.source == "COVER"))).scalars().all()
    assert len(lines) == 1 and Decimal(str(lines[0].amount)) == Decimal("1000.00") and "TM4" in lines[0].label
    covers = (await app_client.get(f"{API}/covers", params={"first": str(D0), "last": str(D0)})).json()
    assert covers[0]["status"] == "Taken" and covers[0]["payroll"] and covers[0]["payable"]
    assert (await app_client.post(f"{API}/covers/{cover_id}/cancel", json={"reason": "x"})).status_code == 422     # on a payroll: locked
    assert (await app_client.patch(f"{API}/covers/{cover_id}", json={"amount": 5})).status_code == 422
    run2, _ = await PayrollService(db_session).prepare(date(2026, 2, 1), actor_id=admin_user.id)
    await db_session.commit()
    assert len((await db_session.execute(select(PayslipLine).where(PayslipLine.source == "COVER"))).scalars().all()) == 1    # never twice


async def test_a_cover_can_be_changed_or_taken_back_before_payroll(app_client, admin_user, db_session, monkeypatch):
    offering_id, studs, absent, sessions = await _class_with_teacher(app_client, db_session, "TM5", n_students=1)
    a = await _employee(app_client, db_session, "SubA", cnic="35202-9400001-1")
    b = await _employee(app_client, db_session, "SubB", cnic="35202-9400002-1")
    _clock(monkeypatch, datetime.combine(D0, time(8, 0)))
    first = (await app_client.post(f"{API}/classes/{sessions[0]}/cover", json={"substitute_id": a["id"], "amount": 700})).json()
    assert first["amount"] == 700.0 and "Set by the administration" in first["amount_note"]
    second = await app_client.post(f"{API}/classes/{sessions[0]}/cover", json={"substitute_id": b["id"]})       # replaces the first
    assert second.status_code == 201
    rows = (await db_session.execute(select(ClassCover).where(ClassCover.class_session_id == sessions[0]))).scalars().all()
    assert sorted(r.status for r in rows) == ["ACTIVE", "CANCELLED"]
    assert (await app_client.patch(f"{API}/covers/{second.json()['id']}", json={"amount": 250})).json()["amount"] == 250.0
    assert (await app_client.post(f"{API}/covers/{second.json()['id']}/cancel", json={"reason": "Teacher came after all"})).status_code == 204
    assert (await app_client.post(f"{API}/classes/{sessions[0]}/cover", json={"substitute_id": absent["id"]})).status_code == 422   # its own teacher


async def test_changing_a_slot_mid_term_keeps_past_classes_and_attendance(app_client, admin_user, db_session, monkeypatch):
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="TT1", n_students=1, cnic_base=4100)
    sid = studs[0][0]
    sessions = await _create_n_sessions(app_client, offering_id, 10)
    for s in sessions[:3]:                                                                    # three classes held
        r = await app_client.post(f"/api/v1/attendance/sessions/{s}/mark", json={"present_student_ids": [sid] if s != sessions[1] else [], "late_note": "test"})
        assert r.status_code == 200, r.text
    before = (await app_client.get(f"/api/v1/attendance/offerings/{offering_id}/students/{sid}/percentage")).json()
    slot = (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/timetable-slots")).json()[0]
    week4 = D0 + timedelta(weeks=4)                                                           # change from the 5th week
    _clock(monkeypatch, datetime.combine(week4 - timedelta(days=1), time(12, 0)))

    pv = (await app_client.get(f"/api/v1/academics/timetable-slots/{slot['id']}/change-preview", params={"from_date": str(week4)})).json()
    assert (pv["classes_before"], pv["classes_before_taken"], pv["classes_from_date"], pv["attendance_marks_kept"]) == (4, 3, 6, 3)
    assert "does not change" in pv["explanation"]

    days = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
    new_day = days[(days.index(SLOT_DAY_OF_WEEK) + 1) % 7]
    bad = await app_client.post(f"/api/v1/academics/timetable-slots/{slot['id']}/change", json={"from_date": str(D0), "day_of_week": new_day,
                                                                                                "period_id": slot["period_id"], "room": "Room 1"})
    assert bad.status_code == 422 and "today or later" in bad.text                            # the past cannot be rewritten
    r = await app_client.post(f"/api/v1/academics/timetable-slots/{slot['id']}/change", json={"from_date": str(week4), "day_of_week": new_day,
                                                                                              "period_id": slot["period_id"], "room": "Room 9"})
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == 6 and r.json()["sessions_generated"] == 5            # 5 Tuesdays left before the term ends and r.json()["slot"]["day_of_week"] == new_day

    all_s = sorted((await app_client.get(f"/api/v1/academics/offerings/{offering_id}/class-sessions")).json(), key=lambda s: s["session_date"])
    old = [s for s in all_s if s["session_date"] < str(week4)]
    new = [s for s in all_s if s["session_date"] >= str(week4)]
    assert [s["id"] for s in old] == sessions[:4]                                             # the same rows, untouched
    assert all(date.fromisoformat(s["session_date"]).weekday() == (D0.weekday() + 1) % 7 and s["room"] == "Room 9" for s in new)
    after = (await app_client.get(f"/api/v1/attendance/offerings/{offering_id}/students/{sid}/percentage")).json()
    assert after == before                                                                    # the student's attendance did not move


async def test_a_slot_added_mid_term_creates_no_classes_in_the_past(app_client, admin_user, db_session, monkeypatch):
    offering_id, _ = await _setup_offering_with_enrolled_students(app_client, code="TT2", n_students=1, cnic_base=4200)
    from tests.integration.test_attendance_and_grading import _get_or_create_period

    period_id = await _get_or_create_period(app_client, "11:00:00", "12:00:00")
    start = D0 + timedelta(weeks=5)
    r = await app_client.post(f"/api/v1/academics/offerings/{offering_id}/timetable-slots",
                              json={"day_of_week": SLOT_DAY_OF_WEEK, "period_id": period_id, "room": "Room 3", "starts_from": str(start)})
    assert r.status_code == 201, r.text
    sessions = (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/class-sessions")).json()
    assert sessions and min(s["session_date"] for s in sessions) >= str(start) and r.json()["sessions_generated"] == len(sessions)