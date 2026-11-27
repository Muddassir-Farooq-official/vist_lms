"""Approval rules (1.72.0): who approves each kind of request. Without rules nothing changes; a rule can say no approval
is needed, or name a role, one person, the head of the department or the course's teacher, by who asks and how much.
Named approvers decide from My decisions; nobody decides their own request; an Administrator can always decide."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import institute_today
from tests.integration.test_access_control import A, _person
from tests.integration.test_fees import _login, _login_admin
from tests.integration.test_smart_timetable import _q as _q  # noqa: F401  (rate limits off)

pytestmark = pytest.mark.asyncio
L = "/api/v1/staff-attendance/leave"
D = "/api/v1/decisions"


def _monday():
    d = institute_today() + timedelta(days=21)
    return d + timedelta(days=(7 - d.weekday()) % 7)


async def _rules(client, action, rules):
    r = await client.put(f"{A}/approval-rules/{action}", json={"rules": rules})
    assert r.status_code == 200, r.text
    return r.json()


async def test_without_rules_nothing_changes_and_the_catalogue_lists_every_action(app_client, admin_user):
    await _login_admin(app_client)
    data = (await app_client.get(f"{A}/approval-rules")).json()
    keys = {a["key"] for a in data["actions"]}
    assert {"ATTENDANCE_CORRECTION", "STAFF_LEAVE", "RESULT_APPROVE", "TIMETABLE_PUBLISH", "REFUND", "FINE_REQUEST", "FINE_WAIVE"} <= keys
    assert all(a["rules"] == [] for a in data["actions"])
    assert (await app_client.get(D)).json() == []


async def test_leave_approved_by_the_principal_from_my_decisions(app_client, admin_user, db_session):
    t = await _person(app_client, db_session, "RLA", "TEACHER", "35202-8200001-1")
    p = await _person(app_client, db_session, "RLB", "PRINCIPAL", "35202-8200002-2")
    office = await _person(app_client, db_session, "RLC", "ACCOUNTS_OFFICER", "35202-8200003-3")
    await app_client.put(f"{A}/people/{office['user_id']}/rights", json={"overrides": [{"code": "staff_attendance.manage", "effect": "GRANT"}]})
    await _rules(app_client, "STAFF_LEAVE", [{"mode": "APPROVE", "approver_type": "ROLE", "approver_value": "PRINCIPAL"}])

    mon = _monday()
    await _login(app_client, t["username"], t["pw"])
    r = await app_client.post(L, data={"kind": "UNPAID", "first_day": str(mon), "last_day": str(mon), "reason": "Family wedding"})
    assert r.status_code == 201, r.text
    leave_id = r.json()["id"]

    # the staff attendance office no longer decides it; the principal does
    await _login(app_client, office["username"], office["pw"])
    refused = await app_client.post(f"{L}/{leave_id}/decide", json={"approve": True})
    assert refused.status_code == 403 and "Principal" in refused.text
    await _login(app_client, p["username"], p["pw"])
    mine = (await app_client.get(D)).json()
    assert [x["id"] for x in mine] == [leave_id] and mine[0]["label"] == "Staff leave"
    assert (await app_client.get(f"{D}/count")).json() == {"waiting": 1}
    assert (await app_client.post(f"{D}/STAFF_LEAVE/{leave_id}", json={"approve": False})).status_code == 422     # reject needs a reason
    ok = await app_client.post(f"{D}/STAFF_LEAVE/{leave_id}", json={"approve": True})
    assert ok.status_code == 200, ok.text
    from app.timekeeping.models import LeaveRequest

    row = (await db_session.execute(select(LeaveRequest).where(LeaveRequest.id == uuid.UUID(leave_id)).execution_options(populate_existing=True))).scalar_one()
    assert row.status == "APPROVED" and str(row.decided_by) == p["user_id"]


async def test_no_approval_needed_applies_at_once_and_is_logged(app_client, admin_user, db_session):
    from app.audit.models import AuditLog

    t = await _person(app_client, db_session, "RLD", "TEACHER", "35202-8200004-4")
    await _rules(app_client, "STAFF_LEAVE", [{"requester_role": "TEACHER", "mode": "NONE"}])
    await _login(app_client, t["username"], t["pw"])
    r = await app_client.post(L, data={"kind": "UNPAID", "first_day": str(_monday()), "last_day": str(_monday()), "reason": "Doctor visit"})
    assert r.status_code == 201 and r.json()["status"] == "APPROVED", r.text
    logged = (await db_session.execute(select(AuditLog).where(AuditLog.action == "approval.not_needed_by_rule"))).scalars().all()
    assert logged


async def test_amount_rules_most_specific_wins_and_try_it(app_client, admin_user, db_session):
    from app.approvals import rules

    acc = await _person(app_client, db_session, "RLE", "ACCOUNTS_OFFICER", "35202-8200005-5")
    p = await _person(app_client, db_session, "RLF", "PRINCIPAL", "35202-8200006-6")
    r = await _rules(app_client, "FINE_REQUEST", [
        {"requester_role": "ACCOUNTS_OFFICER", "mode": "NONE"},
        {"requester_role": "ACCOUNTS_OFFICER", "min_amount": 5000, "mode": "APPROVE", "approver_type": "USER", "approver_value": p["user_id"]},
        {"mode": "APPROVE", "approver_type": "DEFAULT"}])
    assert r["done"] is True                         # the only administrator: a money rule goes ahead (logged)
    small = await rules.effective(db_session, "FINE_REQUEST", requester_id=uuid.UUID(acc["user_id"]), amount=500)
    big = await rules.effective(db_session, "FINE_REQUEST", requester_id=uuid.UUID(acc["user_id"]), amount=5000)
    other = await rules.effective(db_session, "FINE_REQUEST", requester_id=admin_user.id, amount=9000)
    assert small.no_approval and big.approver_type == "USER" and other.default
    tried = (await app_client.post(f"{A}/approval-rules/FINE_REQUEST/try", json={"user_id": acc["user_id"], "amount": 7000})).json()
    assert tried["no_approval"] is False and tried["people"] == ["RLF X"]
    # refused: an approver the action does not offer, a duplicate, an amount on an action without one
    bad = await app_client.put(f"{A}/approval-rules/FINE_REQUEST", json={"rules": [{"mode": "APPROVE", "approver_type": "COURSE_TEACHER"}]})
    assert bad.status_code == 422
    dup = await app_client.put(f"{A}/approval-rules/STAFF_LEAVE", json={"rules": [{"mode": "NONE"}, {"mode": "APPROVE", "approver_type": "DEFAULT"}]})
    assert dup.status_code == 422
    amt = await app_client.put(f"{A}/approval-rules/STAFF_LEAVE", json={"rules": [{"mode": "NONE", "min_amount": 10}]})
    assert amt.status_code == 422


async def test_a_rule_naming_nobody_falls_back_and_the_administrator_can_always_decide(app_client, admin_user, db_session):
    from app.approvals import rules

    t = await _person(app_client, db_session, "RLG", "TEACHER", "35202-8200007-7")
    await _rules(app_client, "STAFF_LEAVE", [{"mode": "APPROVE", "approver_type": "HOD"}])      # nobody is HOD of their department
    res = await rules.effective(db_session, "STAFF_LEAVE", requester_id=uuid.UUID(t["user_id"]), ctx={"department": "Faculty"})
    assert res.default and res.extra.get("fallback")
    hod = await _person(app_client, db_session, "RLH", "HOD", "35202-8200008-8")
    from app.employees.models import Employee

    for uid in (t["user_id"], hod["user_id"]):
        e = (await db_session.execute(select(Employee).where(Employee.user_id == uuid.UUID(uid)))).scalar_one()
        e.department = "Nursing"
    await db_session.commit()
    await _login(app_client, t["username"], t["pw"])
    r = await app_client.post(L, data={"kind": "UNPAID", "first_day": str(_monday()), "last_day": str(_monday()), "reason": "Home visit"})
    leave_id = r.json()["id"]
    await _login(app_client, hod["username"], hod["pw"])
    assert [x["id"] for x in (await app_client.get(D)).json()] == [leave_id]
    await _login_admin(app_client)                                                              # the Administrator can always decide
    assert (await app_client.post(f"{D}/STAFF_LEAVE/{leave_id}", json={"approve": False, "note": "Exams that week"})).status_code == 200


async def test_office_changing_past_attendance_goes_to_the_course_teacher(app_client, admin_user, db_session):
    from app.attendance.models import AttendanceCorrectionRequest
    from tests.integration.test_attendance_and_grading import _create_n_sessions, _setup_offering_with_enrolled_students

    offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="RLX", n_students=1, cnic_base=8300)
    (session_id,) = await _create_n_sessions(app_client, offering_id, 1)
    t = await _person(app_client, db_session, "RLI", "TEACHER", "35202-8200009-9")
    await app_client.patch(f"/api/v1/academics/offerings/{offering_id}", json={"assigned_teacher_id": t["emp"]})
    await _rules(app_client, "ATTENDANCE_CORRECTION", [{"requester_role": "ADMIN", "mode": "APPROVE", "approver_type": "COURSE_TEACHER"}])

    r = await app_client.post(f"/api/v1/attendance/sessions/{session_id}/mark",
                              json={"present_student_ids": [students[0][0]], "late_note": "Register found later"})
    assert r.status_code == 422 and "Sent for approval" in r.text and "teacher" in r.text
    req = (await db_session.execute(select(AttendanceCorrectionRequest))).scalars().all()
    assert len(req) == 1 and req[0].status == "PENDING"

    await _login(app_client, t["username"], t["pw"])
    mine = (await app_client.get(D)).json()
    assert [x["action"] for x in mine] == ["ATTENDANCE_CORRECTION"]
    ok = await app_client.post(f"{D}/ATTENDANCE_CORRECTION/{mine[0]['id']}", json={"approve": True})
    assert ok.status_code == 200, ok.text
    marks = (await app_client.get(f"/api/v1/attendance/sessions/{session_id}")).json()
    assert any(x["status"] == "PRESENT" for x in (marks["records"] if isinstance(marks, dict) else marks))


async def test_rules_about_money_wait_for_a_second_administrator_and_can_be_undone(app_client, admin_user, db_session):
    second = await _person(app_client, db_session, "RLJ", "TEACHER", "35202-8200010-0")
    await app_client.put(f"/api/v1/employees/{second['emp']}/access-roles", json={"extra_roles": ["ADMIN"]})
    r = await _rules(app_client, "REFUND", [{"requester_role": "ACCOUNTS_OFFICER", "mode": "NONE"}])
    assert r["waiting"] is True
    refund = next(a for a in (await app_client.get(f"{A}/approval-rules")).json()["actions"] if a["key"] == "REFUND")
    assert refund["rules"] == []                                                       # nothing changed yet
    waiting = (await app_client.get(f"{A}/waiting")).json()
    await _login(app_client, second["username"], second["pw"])
    assert (await app_client.post(f"{A}/waiting/{waiting[0]['id']}/decide", json={"approve": True})).status_code == 200
    refund = next(a for a in (await app_client.get(f"{A}/approval-rules")).json()["actions"] if a["key"] == "REFUND")
    assert refund["rules"][0]["mode"] == "NONE"
    # a non-money action goes ahead at once; undo puts it back
    await _rules(app_client, "STAFF_LEAVE", [{"mode": "NONE"}])
    change = next(h for h in (await app_client.get(f"{A}/history")).json() if h["kind"] == "APPROVAL_RULES" and "Staff leave" in h["summary"])
    assert (await app_client.post(f"{A}/history/{change['id']}/undo")).json()["done"] is True
    leave = next(a for a in (await app_client.get(f"{A}/approval-rules")).json()["actions"] if a["key"] == "STAFF_LEAVE")
    assert leave["rules"] == []


async def test_decisions_page_opens_in_each_staff_portal(app_client, admin_user):
    await _login_admin(app_client)
    for portal in ("admin", "faculty", "principal", "accounts", "exam"):
        page = await app_client.get(f"/dashboard/{portal}/decisions")
        assert page.status_code == 200 and "decisions.js" in page.text
    assert (await app_client.get("/dashboard/student/decisions")).status_code == 404
