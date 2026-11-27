"""Fixes from the November 2026 audit (1.68.0): leavers paid for days worked (resigned without 30 days' notice: withheld);
a student who leaves loses hostel, transport and mentor; clash messages name the class; Dropped / Graduated can be
undone by an administrator within 30 days; fees missed while suspended are offered when the suspension is lifted."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.clock import institute_today
from app.employees.models import Employee, EmployeeStatus
from app.hostel.models import HostelStay
from app.payroll import rules
from app.students.models import StudentStatusHistory
from app.transport.models import TransportRider
from tests.integration.test_attendance_and_grading import _create_and_activate_student, _login_admin
from tests.integration.test_payroll import PR, THIS, _emp
from tests.integration.test_payroll import _quiet as _quiet  # noqa: F401  (rate limits off)

pytestmark = pytest.mark.asyncio
TODAY = institute_today()


def test_days_paid_and_notice_arithmetic():
    nov = date(2026, 11, 1)
    assert rules.payable_days(date(2026, 1, 1), nov, date(2026, 11, 15)) == 15
    assert rules.payable_days(date(2026, 11, 10), nov, date(2026, 11, 15)) == 6
    assert rules.payable_days(date(2026, 1, 1), nov, date(2026, 10, 31)) == 0
    assert rules.notice_short("RESIGNED", date(2026, 11, 5), date(2026, 11, 15), 30) == 20
    assert rules.notice_short("RESIGNED", date(2026, 10, 1), date(2026, 11, 15), 30) == 0
    assert rules.notice_short("RESIGNED", None, date(2026, 11, 15), 30) == 30
    assert rules.notice_short("TERMINATED", date(2026, 11, 14), date(2026, 11, 15), 30) == 0


async def test_leavers_are_paid_for_days_worked_unless_they_resigned_without_notice(app_client, admin_user, db_session):
    await _login_admin(app_client)
    quit_ = await _emp(app_client, db_session, "QUIT", 60000, "35202-7600001-1")
    fired = await _emp(app_client, db_session, "FIRED", 30000, "35202-7600002-2")
    good = await _emp(app_client, db_session, "NOTICE", 45000, "35202-7600003-3")
    E = "/api/v1/employees"
    r = await app_client.post(f"{E}/{quit_['id']}/status", json={"new_status": "TERMINATED", "reason": "Resigned", "leaving_type": "RESIGNED",
                                                               "left_on": str(TODAY), "notice_given_on": str(TODAY - timedelta(days=10))})
    assert r.status_code == 200, r.text
    assert (await app_client.post(f"{E}/{fired['id']}/status", json={"new_status": "TERMINATED", "reason": "Misconduct",
                                                                     "leaving_type": "TERMINATED", "left_on": str(TODAY)})).status_code == 200
    assert (await app_client.post(f"{E}/{good['id']}/status", json={"new_status": "TERMINATED", "reason": "Resigned", "leaving_type": "RESIGNED",
                                                                    "left_on": str(TODAY), "notice_given_on": str(TODAY - timedelta(days=40))})).status_code == 200
    # a resignation needs the day notice was given
    other = await _emp(app_client, db_session, "NONOTE", 20000, "35202-7600004-4")
    assert (await app_client.post(f"{E}/{other['id']}/status", json={"new_status": "TERMINATED", "leaving_type": "RESIGNED"})).status_code == 422

    run = await app_client.post(f"{PR}/runs", json={"month": str(THIS)})
    assert run.status_code == 201, run.text
    slips = {s["employee_id"]: s for s in run.json()["payslips"]}
    days = TODAY.day
    dim = rules.days_in(THIS)
    for who, salary in ((quit_, 60000), (fired, 30000), (good, 45000)):
        assert who["id"] in slips, "a leaver this month has a payslip"
        detail = (await app_client.get(f"{PR}/payslips/{slips[who['id']]['id']}")).json()
        base = next(x for x in detail["earnings"] if x["source"] == "BASE")
        assert base["amount"] == pytest.approx(float(rules.prorate(Decimal(salary), days, dim)), abs=0.01)
        notice = [x for x in detail["deduction_lines"] if x["source"] == "NOTICE"]
        if who is quit_:
            assert notice and notice[0]["amount"] == pytest.approx(base["amount"], abs=0.01) and "20 days short" in notice[0]["label"]
        else:
            assert not notice
    assert (await db_session.get(Employee, uuid.UUID(quit_["id"]))).status == EmployeeStatus.TERMINATED


async def test_a_student_who_leaves_loses_hostel_transport_and_can_be_undone_within_30_days(app_client, admin_user, db_session):
    a, _u = await _create_and_activate_student(app_client, cnic="76301-0000001-1")
    await _login_admin(app_client)
    month = TODAY.replace(day=1)
    room = (await app_client.post("/api/v1/hostel/rooms", json={"code": "Z1", "capacity": 2, "monthly_fee": 8000})).json()
    assert (await app_client.post("/api/v1/hostel/stays", json={"room_id": room["id"], "student_id": a, "start_date": str(month)})).status_code == 201
    route = (await app_client.post("/api/v1/transport/routes", json={"name": "Z route", "seats": 5, "monthly_fee": 3000})).json()
    assert (await app_client.post("/api/v1/transport/riders", json={"route_id": route["id"], "student_id": a, "start_date": str(month)})).status_code == 201

    r = await app_client.post(f"/api/v1/students/{a}/status", json={"new_status": "DROPPED", "reason": "Expelled"})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    stay = (await db_session.execute(select(HostelStay).where(HostelStay.student_id == uuid.UUID(a)))).scalar_one()
    seat = (await db_session.execute(select(TransportRider).where(TransportRider.student_id == uuid.UUID(a)))).scalar_one()
    assert stay.end_date == TODAY and seat.end_date == TODAY

    # undo: a reason is needed; within 30 days it works
    assert (await app_client.post(f"/api/v1/students/{a}/status", json={"new_status": "ACTIVE", "reason": "oops"})).status_code == 422
    ok = await app_client.post(f"/api/v1/students/{a}/status", json={"new_status": "ACTIVE", "reason": "Expelled the wrong student by mistake"})
    assert ok.status_code == 200, ok.text
    # older than 30 days: refused, with the way out
    assert (await app_client.post(f"/api/v1/students/{a}/status", json={"new_status": "DROPPED", "reason": "Left"})).status_code == 200
    for h in (await db_session.execute(select(StudentStatusHistory).where(StudentStatusHistory.student_id == uuid.UUID(a)))).scalars():
        h.changed_at = h.changed_at - timedelta(days=31)           # every change happened more than 30 days ago
    await db_session.commit()
    late = await app_client.post(f"/api/v1/students/{a}/status", json={"new_status": "ACTIVE", "reason": "Wants to come back next year"})
    assert late.status_code == 422 and "30 days" in late.text
    # and a plain impossible change says what is possible
    msg = (await app_client.post(f"/api/v1/students/{a}/status", json={"new_status": "SUSPENDED"})).json()["error"]["message"]
    assert "Dropped" in msg and "undo" in msg


async def test_fees_assigned_while_suspended_are_offered_on_lifting(app_client, admin_user, db_session):
    from tests.integration.test_batch_terms import ALL_BATCHES

    a, _u = await _create_and_activate_student(app_client, cnic="76302-0000001-1")
    b, _v = await _create_and_activate_student(app_client, cnic="76302-0000002-2")
    await _login_admin(app_client)
    assert (await app_client.post(f"/api/v1/students/{b}/status", json={"new_status": "SUSPENDED", "reason": "Discipline"})).status_code == 200
    fee_type = (await app_client.post("/api/v1/fees/types", json={"code": "SUSF", "name": "Semester Fee"})).json()["id"]
    s = await app_client.post("/api/v1/fees/structures", json={"fee_type_id": fee_type, "total_amount": 1000, "installment_count": 1,
                                                                 "effective_from": str(TODAY), "name": "Semester fee", "term_label": ALL_BATCHES})
    assert s.status_code == 201, s.text
    assert (await app_client.post(f"/api/v1/fees/structures/{s.json()['id']}/activate")).status_code == 200
    assert (await app_client.get(f"/api/v1/students/{a}/missed-fees")).json() == []          # never suspended
    assert (await app_client.post(f"/api/v1/students/{b}/status", json={"new_status": "ACTIVE", "reason": "Lifted"})).status_code == 200
    missed = (await app_client.get(f"/api/v1/students/{b}/missed-fees")).json()
    assert [m["name"] for m in missed] == ["Semester fee"]
    r = await app_client.post(f"/api/v1/students/{b}/missed-fees", json={"fee_structure_ids": [missed[0]["fee_structure_id"]]})
    assert r.status_code == 200 and r.json()["assigned"] == 1
    assert (await app_client.get(f"/api/v1/students/{b}/missed-fees")).json() == []


async def test_clash_messages_name_the_class_and_the_time(app_client, admin_user, db_session):
    from tests.integration.test_smart_timetable import A, _setup

    offs, _t, _l, periods = await _setup(app_client, db_session)
    assert (await app_client.post(f"{A}/offerings/{offs['101A']}/timetable-slots", json={"day_of_week": "MONDAY", "period_id": periods[0], "room": "SR1"})).status_code == 201
    r = await app_client.post(f"{A}/offerings/{offs['102A']}/timetable-slots", json={"day_of_week": "MONDAY", "period_id": periods[0], "room": "SR1"})
    msg = r.json()["error"]["message"]
    assert r.status_code == 422 and "SM101" in msg and "Monday" in msg and ("AM" in msg or "PM" in msg) and ":00:00" not in msg


async def test_clinical_sheet_locks_suspended_and_drops_expelled(app_client, admin_user, db_session):
    from app.students.models import Student

    C = "/api/v1/clinical"
    studs = [await _create_and_activate_student(app_client, cnic=f"35202-77900{i:02d}-1", contact_no=f"0300779{i:04d}") for i in range(3)]
    await _login_admin(app_client)
    rolls = [(await db_session.get(Student, uuid.UUID(s))).roll_number for s, _u in studs]
    site = (await app_client.post(f"{C}/sites", json={"name": "Audit Hospital"})).json()["id"]
    ward = (await app_client.post(f"{C}/sites/{site}/units", json={"name": "Ward", "capacity": 5})).json()["id"]
    g = (await app_client.post(f"{C}/groups", json={"name": "Audit group", "roll_numbers": rolls})).json()["id"]
    rot = await app_client.post(f"{C}/rotations", json={"group_id": g, "unit_id": ward, "supervisor_id": None, "start_date": str(TODAY - timedelta(days=2)),
                                                       "end_date": str(TODAY + timedelta(days=5)), "days": "0,1,2,3,4,5,6", "start_time": "08:00", "end_time": "14:00"})
    assert rot.status_code == 200, rot.text
    r = rot.json()["id"]
    assert (await app_client.post(f"/api/v1/students/{studs[1][0]}/status", json={"new_status": "SUSPENDED", "reason": "Discipline"})).status_code == 200
    assert (await app_client.post(f"/api/v1/students/{studs[2][0]}/status", json={"new_status": "DROPPED", "reason": "Expelled"})).status_code == 200
    sheet = (await app_client.get(f"{C}/supervise/{r}/day", params={"day": str(TODAY)})).json()
    by = {s["id"]: s for s in sheet["students"]}
    assert set(by) == {studs[0][0], studs[1][0]} and by[studs[1][0]]["suspended"] and not by[studs[0][0]]["suspended"]
    marks = [{"student_id": s, "status": "PRESENT"} for s, _u in studs[:2]]
    assert (await app_client.post(f"{C}/supervise/{r}/attendance", json={"day": str(TODAY), "entries": marks})).json()["marked"] == 1


async def test_exam_roll_bars_a_suspended_student_and_the_duty_count_leaves_them_out(app_client, admin_user):
    from tests.integration.test_attendance_and_grading import _setup_offering_with_enrolled_students
    from tests.integration.test_exam_datesheets import E, _paper, _schedule, _term_of

    o1, students = await _setup_offering_with_enrolled_students(app_client, code="AUDX", n_students=2, cnic_base=6500)
    off = await _term_of(app_client, o1)
    sched = await _schedule(app_client, off["term_label"])
    p = await app_client.post(f"{E}/schedules/{sched}/papers", json=_paper(o1, off["term_start_date"][:8] + "22"))
    assert p.status_code == 201, p.text
    pid = p.json()["papers"][0]["id"]
    assert (await app_client.post(f"/api/v1/students/{students[1][0]}/status", json={"new_status": "SUSPENDED", "reason": "Discipline"})).status_code == 200
    roll = (await app_client.get(f"{E}/papers/{pid}/roll")).json()
    barred = {b["student_id"]: b for b in roll["barred"]}
    assert students[1][0] in barred and "suspended" in barred[students[1][0]]["reasons"]
    assert students[0][0] not in barred
    assert (await app_client.get(f"{E}/papers/{pid}/duty")).json()["students"] == 1


async def test_a_duplicate_save_gets_a_plain_message_not_an_unexpected_error():
    import json

    from sqlalchemy.exc import IntegrityError
    from starlette.requests import Request

    from app.main import integrity_error_handler

    class Orig(Exception):
        sqlstate = "23505"

        class diag:
            constraint_name = "uq_class_sessions_offering_date_start"

    req = Request({"type": "http", "method": "POST", "path": "/api/v1/x", "headers": [], "query_string": b""})
    resp = await integrity_error_handler(req, IntegrityError("INSERT", {}, Orig("duplicate key value")))
    body = json.loads(resp.body)
    assert resp.status_code == 409 and body["error"]["code"] == "DUPLICATE"
    assert "already exists" in body["error"]["message"] and "class sessions offering date start" in body["error"]["message"]
