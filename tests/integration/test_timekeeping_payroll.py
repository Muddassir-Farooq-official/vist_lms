"""Staff timekeeping feeding payroll: timings (default / job role / employee), corrections, holidays, CSV import,
absences with casual leave (teachers none, staff 2), every N late / early marks = 1 day, working days per run,
manual overrides, security deposit until it equals the basic salary."""
from __future__ import annotations

import io
from datetime import datetime, timedelta

import pytest

from app.core.clock import institute_today
from app.core.config import settings
from app.payroll import rules as pay_rules
from app.timekeeping import rules
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_fees import _login_admin
from tests.integration.test_payroll import _emp

pytestmark = pytest.mark.asyncio
SA = "/api/v1/staff-attendance"
PR = "/api/v1/payroll"
LAST = (institute_today().replace(day=1) - timedelta(days=1)).replace(day=1)        # the whole of last month is in the past
LAST_DAYS = pay_rules.days_in(LAST)
ALL_WEEK = {"in_time": "09:00", "out_time": "17:00", "grace_in_minutes": 10, "grace_out_minutes": 5, "working_days": [0, 1, 2, 3, 4, 5, 6]}

async def _approve(client, run_id):
    """Payroll now goes DRAFT -> SUBMITTED (accounts office) -> APPROVED (administration). Submit, then approve."""
    sub = await client.post(f"/api/v1/payroll/runs/{run_id}/submit")
    if sub.status_code != 200 and "Only a draft" not in sub.text:     # already submitted is fine; a real problem is returned
        return sub
    return await client.post(f"/api/v1/payroll/runs/{run_id}/approve")



@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


def test_the_timing_arithmetic():
    d = datetime(2026, 10, 5, 9, 10, 59)
    assert rules.late_minutes(d, datetime(2026, 1, 1, 9, 0).time(), 10, True) == 0                     # inside the relaxation
    assert rules.late_minutes(d.replace(minute=11, second=0), datetime(2026, 1, 1, 9, 0).time(), 10, True) == 11   # counted from 09:00
    assert rules.late_minutes(d.replace(minute=40), datetime(2026, 1, 1, 9, 0).time(), 10, False) == 0                # not a working day
    assert rules.early_minutes(d.replace(hour=16, minute=56), datetime(2026, 1, 1, 17, 0).time(), 5, True) == 0
    assert rules.early_minutes(d.replace(hour=16, minute=30, second=0), datetime(2026, 1, 1, 17, 0).time(), 5, True) == 30
    assert [rules.marks_to_days(n, 4) for n in (0, 3, 4, 7, 8, 13)] == [0, 0, 1, 1, 2, 3]


def _csv(rows):
    return ("employee_code,date,check_in,check_out\n" + "\n".join(",".join(r) for r in rows)).encode()


async def _month_rows(code, *, absent=(), late=(), early=(), no_out=()):
    rows = []
    for day in range(1, LAST_DAYS + 1):
        if day in absent:
            continue
        d = LAST.replace(day=day).isoformat()
        rows.append((code, d, "09:30" if day in late else "08:55", "" if day in no_out else ("16:00" if day in early else "17:05")))
    return rows


async def _setup(client, db):
    await _login_admin(client)
    t = await _emp(client, db, "TCH", 60000, "35202-7700001-1", "TEACHER")
    s = await _emp(client, db, "STF", 30000, "35202-7700002-2", "LIBRARIAN")
    n = await _emp(client, db, "NON", 26000, "35202-7700003-3", "LIBRARIAN")
    assert (await client.put(f"{SA}/timings/default", json=ALL_WEEK)).status_code == 204
    board = (await client.get(f"{SA}/board")).json()["people"]
    codes = {p["name"].split()[0]: p["employee_code"] for p in board}
    return t, s, n, codes


async def _import(client, rows):
    r = await client.post(f"{SA}/import", params={"dry_run": "false"}, files={"file": ("a.csv", io.BytesIO(_csv(rows)), "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["errors"] == [], r.json()
    return r.json()


async def test_timings_resolve_from_employee_then_job_role_then_default(app_client, admin_user, db_session):
    t, s, n, codes = await _setup(app_client, db_session)
    timings = (await app_client.get(f"{SA}/timings")).json()
    assert timings["default"]["in_time"] == "09:00" and timings["default"]["working_days"] == [0, 1, 2, 3, 4, 5, 6]
    teacher_role = next(j for j in timings["job_roles"] if j["is_teacher"] and j["title"] == "TCH title")
    assert (await app_client.put(f"{SA}/timings/job-roles/{teacher_role['job_role_id']}", json={**ALL_WEEK, "in_time": "08:00", "out_time": "14:00"})).status_code == 204
    assert (await app_client.put(f"{SA}/timings/employees/{s['id']}", json={**ALL_WEEK, "in_time": "10:00"})).status_code == 204
    bad = await app_client.put(f"{SA}/timings/employees/{s['id']}", json={**ALL_WEEK, "out_time": "08:00"})
    assert bad.status_code == 422
    board = {p["name"].split()[0]: p["timing"] for p in (await app_client.get(f"{SA}/board")).json()["people"]}
    assert (board["TCH"]["in_time"], board["TCH"]["source"]) == ("08:00", "JOB_ROLE")
    assert (board["STF"]["in_time"], board["STF"]["source"]) == ("10:00", "EMPLOYEE")
    assert (board["NON"]["in_time"], board["NON"]["source"]) == ("09:00", "DEFAULT")
    assert (await app_client.delete(f"{SA}/timings/employees/{s['id']}")).status_code == 204
    board = {p["name"].split()[0]: p["timing"] for p in (await app_client.get(f"{SA}/board")).json()["people"]}
    assert board["STF"]["source"] == "DEFAULT"
    assert (await app_client.delete(f"{SA}/timings/job-roles/{teacher_role['job_role_id']}")).status_code == 204


async def test_payroll_deducts_absences_casual_leave_and_late_marks(app_client, admin_user, db_session):
    t, s, n, codes = await _setup(app_client, db_session)
    await _import(app_client, await _month_rows(codes["TCH"], absent={3, 4, 5}, late={6, 7, 8, 9, 10}))             # teacher: 3 absent, 5 late
    await _import(app_client, await _month_rows(codes["STF"], absent={3, 4}, early={6, 7, 8}, no_out={9}))          # staff: 2 absent, 3 early + 1 no check-out
    month = (await app_client.get(f"{SA}/month", params={"month": LAST.isoformat()})).json()
    m = {x["name"].split()[0]: x for x in month}
    assert (m["TCH"]["scheduled"], m["TCH"]["present"], m["TCH"]["absent"], m["TCH"]["late"]) == (LAST_DAYS, LAST_DAYS - 3, 3, 5)
    assert (m["STF"]["absent"], m["STF"]["early"], m["STF"]["missing_out"]) == (2, 3, 1)
    assert m["NON"]["recorded"] == 0

    r = await app_client.post(f"{PR}/runs", json={"month": LAST.isoformat(), "working_days": 26})
    assert r.status_code == 201, r.text
    run = (await app_client.get(f"{PR}/runs/{r.json()['id']}")).json()
    slips = {x["employee_name"].split()[0]: x for x in run["payslips"]}
    tch, stf, non = slips["TCH"], slips["STF"], slips["NON"]
    # teacher: no casual leave -> 3 absent + 5 marks (= 1 day) -> 4 days of 60000 / 26
    assert (tch["working_days"], tch["absent_days"], tch["casual_leaves"], tch["late_marks"], tch["late_days"]) == (26, 3.0, 0.0, 5, 1)
    assert tch["net"] == pytest.approx(60000 - float(pay_rules.unpaid_deduction(60000, 26, 3)) - float(pay_rules.unpaid_deduction(60000, 26, 1)), abs=0.01)
    # staff: 2 casual leaves forgive both absences; 3 early + 1 missing check-out = 4 marks = 1 day
    assert (stf["absent_days"], stf["casual_leaves"], stf["late_marks"], stf["late_days"]) == (2.0, 2.0, 4, 1)
    assert stf["net"] == pytest.approx(30000 - float(pay_rules.unpaid_deduction(30000, 26, 1)), abs=0.01)
    # nothing recorded: never treated as absent all month
    assert non["absent_days"] == 0 and non["net"] == 26000.0 and "No attendance recorded" in non["attendance_note"]
    lines = (await app_client.get(f"{PR}/payslips/{tch['id']}")).json()["deduction_lines"]
    assert [ln["source"] for ln in lines] == ["ABSENT", "LATE"] and "every 4 = 1 day" in lines[1]["label"]

    # the administration overrides, then goes back to the attendance figures
    run = (await app_client.patch(f"{PR}/runs/{run['id']}/payslips/{tch['id']}", json={"absent_days": 1, "late_marks": 0, "working_days": 30})).json()
    tch = next(x for x in run["payslips"] if x["id"] == tch["id"])
    assert (tch["absent_days"], tch["late_marks"], tch["working_days"], tch["attendance_override"]) == (1.0, 0, 30, True)
    assert tch["net"] == pytest.approx(60000 - float(pay_rules.unpaid_deduction(60000, 30, 1)), abs=0.01)
    run = (await app_client.patch(f"{PR}/runs/{run['id']}/payslips/{tch['id']}", json={"reset_attendance": True, "adjustment": 2500, "adjustment_note": "Compensation for exam duty"})).json()
    tch = next(x for x in run["payslips"] if x["id"] == tch["id"])
    assert (tch["absent_days"], tch["late_marks"], tch["attendance_override"]) == (3.0, 5, False)
    assert tch["gross"] == 62500.0

    # the rules can be changed any time: 5 marks per day now, and staff get 1 casual leave
    settings.payroll_late_marks_per_day, settings.payroll_casual_leaves_staff = 5, 1
    try:
        run = (await app_client.post(f"{PR}/runs/{run['id']}/recalculate")).json()
    finally:
        settings.payroll_late_marks_per_day, settings.payroll_casual_leaves_staff = 4, 2
    slips = {x["employee_name"].split()[0]: x for x in run["payslips"]}
    assert (slips["TCH"]["late_days"], slips["STF"]["late_days"], slips["STF"]["casual_leaves"]) == (1, 0, 1.0)


async def test_corrections_excuses_and_holidays(app_client, admin_user, db_session):
    t, s, n, codes = await _setup(app_client, db_session)
    day = LAST.replace(day=10).isoformat()
    r = await app_client.put(f"{SA}/days/{t['id']}/{day}", json={"check_in": "09:45", "check_out": "16:30"})
    assert r.status_code == 200 and r.json()["day"]["late_minutes"] == 45 and r.json()["day"]["early_minutes"] == 30
    assert (await app_client.put(f"{SA}/days/{t['id']}/{day}", json={"excused": True})).status_code == 422          # needs a reason
    r = await app_client.put(f"{SA}/days/{t['id']}/{day}", json={"excused": True, "excuse_reason": "Official duty: board exams"})
    assert r.json()["day"]["excused"] and r.json()["day"]["late_minutes"] == 0 and r.json()["state"] == "EXCUSED"
    assert (await app_client.put(f"{SA}/days/{t['id']}/{(institute_today() + timedelta(days=1)).isoformat()}", json={"check_in": "09:00"})).status_code == 422
    held = await app_client.post(f"{SA}/holiday", json={"day": LAST.replace(day=11).isoformat(), "reason": "Public holiday"})
    assert held.status_code == 200 and held.json()["employees"] == 3
    one = (await app_client.get(f"{SA}/month/{s['id']}", params={"month": LAST.isoformat()})).json()
    # counting starts on day 10, the first day anything was recorded: days 10..end, day 11 excused, the rest absent
    assert one["summary"]["excused"] == 1 and one["summary"]["present"] == 1 and one["summary"]["absent"] == LAST_DAYS - 10
    bad = await app_client.post(f"{SA}/import", params={"dry_run": "true"}, files={"file": ("a.csv", io.BytesIO(_csv([("NOPE", day, "09:00", ""), (codes["TCH"], "2026-13-01", "09:00", "")])), "text/csv")})
    assert [e["row"] for e in bad.json()["errors"]] == [2, 3]


async def test_guard_marks_in_and_out_but_cannot_correct(app_client, admin_user, db_session):
    t, s, n, codes = await _setup(app_client, db_session)
    guard, first = await _staff(app_client, db_session, "GRD", "SECURITY_GUARD", "35202-7700009-9")
    await _first_login(app_client, guard, first)
    marked = await app_client.post(f"{SA}/mark", json={"employee_id": t["id"], "action": "IN"})
    assert marked.status_code == 200 and marked.json()["state"] == "IN"
    assert (await app_client.post(f"{SA}/mark", json={"employee_id": t["id"], "action": "IN"})).status_code == 422
    assert (await app_client.post(f"{SA}/mark", json={"employee_id": s["id"], "action": "OUT"})).status_code == 422          # not in yet
    assert (await app_client.post(f"{SA}/undo/{t['id']}")).json()["state"] == "NOT_IN"
    assert (await app_client.put(f"{SA}/days/{t['id']}/{LAST.isoformat()}", json={"check_in": "09:00"})).status_code == 403
    assert (await app_client.get(f"{SA}/month", params={"month": LAST.isoformat()})).status_code == 403
    assert (await app_client.get(f"{SA}/board", params={"day": LAST.isoformat()})).status_code == 422


async def test_security_deposit_is_held_until_it_equals_the_basic_salary(app_client, admin_user, db_session):
    t, s, n, codes = await _setup(app_client, db_session)
    settings.payroll_security_percent = 10
    try:
        assert (await app_client.put(f"{PR}/settings/{t['id']}", json={"security_percent": None, "security_opening": 55000})).status_code == 200
        assert (await app_client.put(f"{PR}/settings/{n['id']}", json={"security_percent": 0})).status_code == 200
        run = (await app_client.post(f"{PR}/runs", json={"month": LAST.isoformat()})).json()
        slips = {x["employee_name"].split()[0]: x for x in (await app_client.get(f"{PR}/runs/{run['id']}")).json()["payslips"]}
        got = {k: (await app_client.get(f"{PR}/payslips/{v['id']}")).json()["deduction_lines"] for k, v in slips.items()}

        def lines(who):
            return {ln["source"]: ln["amount"] for ln in got[who]}
        assert lines("TCH") == {"SECURITY": 5000.0}                          # 10% = 6,000 but only 5,000 was still to hold
        assert lines("STF") == {"SECURITY": 3000.0} and lines("NON") == {}  # 10% of 30,000; NON is excused (0%)
        assert (await _approve(app_client, run['id'])).status_code == 200
        paid = await app_client.post(f"{PR}/runs/{run['id']}/pay", json={"payment_method": "CASH", "paid_date": institute_today().isoformat()})
        assert paid.status_code == 200, paid.text
        rep = {p["name"].split()[0]: p for p in (await app_client.get(f"{PR}/security")).json()["people"]}
        assert (rep["TCH"]["held"], rep["TCH"]["remaining"], rep["STF"]["held"], rep["NON"]["own_percent"]) == (60000.0, 0.0, 3000.0, 0.0)
        nxt = (await app_client.post(f"{PR}/runs", json={"month": institute_today().replace(day=1).isoformat()})).json()
        slips = {x["employee_name"].split()[0]: x for x in (await app_client.get(f"{PR}/runs/{nxt['id']}")).json()["payslips"]}
        tch_lines = (await app_client.get(f"{PR}/payslips/{slips['TCH']['id']}")).json()["deduction_lines"]
        assert not any(ln["source"] == "SECURITY" for ln in tch_lines)       # fully held: nothing more taken
    finally:
        settings.payroll_security_percent = 0.0


async def test_only_the_right_people_see_staff_attendance(app_client, admin_user, db_session):
    t, s, n, codes = await _setup(app_client, db_session)
    await _first_login(app_client, t["username"], t["pw"])
    assert (await app_client.get(f"{SA}/board")).status_code == 403
    assert (await app_client.get(f"{PR}/security")).status_code == 403