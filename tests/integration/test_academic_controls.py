"""Academic controls: the withdrawal deadline and W, approved leave, separation of duties on results, the
not-submitted list, moving section, deleting an empty offering, and CSV cells that cannot run as formulas."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.academics.models import ResultSheet, ResultSheetStatus
from app.academics.result_sheet_service import ResultSheetService
from app.academics.service import EnrollmentService
from app.attendance.service import AttendanceService
from app.core.config import settings
from app.core.csvsafe import safe_cell
from app.core.exceptions import BusinessRuleViolationError
from tests.integration.test_attendance_and_grading import TERM_END, TERM_START, _create_n_sessions, _login_admin, _setup_offering_with_enrolled_students

pytestmark = pytest.mark.asyncio
AC = "/api/v1/academics"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


def test_csv_cells_cannot_run_as_formulas():
    assert safe_cell("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")"
    assert safe_cell("@SUM(A1)") == "'@SUM(A1)" and safe_cell("-cmd") == "'-cmd"
    assert safe_cell("+923001234567") == "+923001234567" and safe_cell("-5") == "-5" and safe_cell("Ali") == "Ali" and safe_cell(12) == 12


async def test_after_the_deadline_only_the_office_withdraws_and_it_shows_as_w(app_client, admin_user, db_session, monkeypatch):
    monkeypatch.setattr(settings, "withdrawal_deadline_days", 14)                 # the test term began in January
    offering, students = await _setup_offering_with_enrolled_students(app_client, code="WDL", n_students=2, cnic_base=8400)
    (s1, _u1, e1), (s2, _u2, e2) = students
    with pytest.raises(BusinessRuleViolationError, match="deadline"):
        await EnrollmentService(db_session).withdraw(uuid.UUID(e1), student_id=uuid.UUID(s1), actor_id=admin_user.id)
    await _login_admin(app_client)
    r = await app_client.post(f"{AC}/enrollments/{e1}/admin-withdraw", json={"reason": "Long illness"})
    assert r.status_code == 200 and r.json()["status"] == "WITHDRAWN"
    t = (await app_client.get(f"/api/v1/results/students/{s1}/transcript")).json()
    c = [c for term in t["terms"] for c in term["courses"]][0]
    assert c["letter_grade"] == "W" and "deadline" in c["withdrawn_reason"]


async def test_approved_leave_is_not_counted_against_attendance(app_client, admin_user, db_session):
    offering, students = await _setup_offering_with_enrolled_students(app_client, code="LVE", n_students=1, cnic_base=8500)
    sid = uuid.UUID(students[0][0])
    await _login_admin(app_client)
    await _create_n_sessions(app_client, offering, 3, room="Room L1")
    svc = AttendanceService(db_session)
    from app.academics.models import ClassSession

    sessions = (await db_session.execute(select(ClassSession).where(ClassSession.course_offering_id == uuid.UUID(offering)).order_by(ClassSession.session_date))).scalars().all()
    if len(sessions) < 2:
        pytest.skip("the helper made no class sessions")
    await svc.mark_session(sessions[0].id, present_student_ids=[], actor_id=admin_user.id, actor_is_admin=True, late_note="test")
    assert (await svc.compute_percentage(sid, uuid.UUID(offering)))["percentage"] == 0.0
    res = await svc.grant_leave(sid, from_date=TERM_START, to_date=TERM_END, reason="Medical certificate 123", actor_id=admin_user.id)
    assert res["classes_marked_leave"] >= 1
    pct = await svc.compute_percentage(sid, uuid.UUID(offering))
    assert pct["leave_count"] >= 1 and pct["total_sessions_taken"] == 0 and pct["percentage"] == 100.0
    # a later register leaves the leave in place unless the student is marked present
    await svc.mark_session(sessions[0].id, present_student_ids=[], actor_id=admin_user.id, actor_is_admin=True, late_note="test")
    assert (await svc.compute_percentage(sid, uuid.UUID(offering)))["leave_count"] >= 1


async def test_whoever_submits_marks_does_not_approve_them(app_client, admin_user, db_session):
    offering, _students = await _setup_offering_with_enrolled_students(app_client, code="SOD", n_students=1, cnic_base=8600)
    sheet = ResultSheet(course_offering_id=uuid.UUID(offering), status=ResultSheetStatus.SUBMITTED, submitted_by=admin_user.id)
    db_session.add(sheet)
    await db_session.commit()
    # the only person who can approve results: allowed, so work never gets stuck (the approval is recorded as such)
    from app.auth.permissions import Permissions
    from app.core.controls import four_eyes

    assert await four_eyes(db_session, admin_user.id, [admin_user.id], Permissions.RESULT_APPROVE, "x") is True
    assert await four_eyes(db_session, admin_user.id, [None], Permissions.RESULT_APPROVE, "x") is False

    # once the exam office exists, the person who submitted must leave approval to them
    from tests.integration.test_announcements import _staff

    await _login_admin(app_client)
    await _staff(app_client, db_session, "EXM", "EXAM_CONTROLLER", "35202-8600099-9")
    offering2, _ = await _setup_offering_with_enrolled_students(app_client, code="SOE", n_students=1, cnic_base=8650)
    db_session.add(ResultSheet(course_offering_id=uuid.UUID(offering2), status=ResultSheetStatus.SUBMITTED, submitted_by=admin_user.id))
    await db_session.commit()
    with pytest.raises(BusinessRuleViolationError, match="another approver"):
        await ResultSheetService(db_session).approve(uuid.UUID(offering2), actor_id=admin_user.id)


async def test_courses_without_submitted_marks_are_listed_for_the_exam_office(app_client, admin_user):
    offering, _students = await _setup_offering_with_enrolled_students(app_client, code="OUT", n_students=2, cnic_base=8700)
    await _login_admin(app_client)
    rows = (await app_client.get("/api/v1/results/sheets-outstanding")).json()
    row = next(r for r in rows if r["course_offering_id"] == offering)
    assert row["students"] == 2 and row["status"] == "DRAFT"


async def test_an_empty_offering_can_be_deleted_but_not_one_with_students(app_client, admin_user):
    offering, _students = await _setup_offering_with_enrolled_students(app_client, code="DEL", n_students=1, cnic_base=8800)
    await _login_admin(app_client)
    course_id = (await app_client.get(f"{AC}/offerings/{offering}")).json()["course_id"]
    empty = (await app_client.post(f"{AC}/offerings", json={"course_id": course_id, "term_label": "Fall 2026", "section_label": "Z",
                                                          "term_start_date": str(TERM_START), "term_end_date": str(TERM_END)})).json()
    assert (await app_client.delete(f"{AC}/offerings/{offering}")).status_code == 422
    assert (await app_client.delete(f"{AC}/offerings/{empty['id']}")).status_code == 204
    assert (await app_client.get(f"{AC}/offerings/{empty['id']}")).status_code == 404


async def test_a_student_can_move_section_before_anything_is_recorded(app_client, admin_user):
    offering, students = await _setup_offering_with_enrolled_students(app_client, code="MOV", n_students=1, cnic_base=8900)
    await _login_admin(app_client)
    course_id = (await app_client.get(f"{AC}/offerings/{offering}")).json()["course_id"]
    other = (await app_client.post(f"{AC}/offerings", json={"course_id": course_id, "term_label": "Fall 2026", "section_label": "B",
                                                          "term_start_date": str(TERM_START), "term_end_date": str(TERM_END)})).json()
    e = students[0][2]
    secs = (await app_client.get(f"{AC}/enrollments/{e}/sections")).json()
    assert [s["section_label"] for s in secs] == ["B"]
    r = await app_client.post(f"{AC}/enrollments/{e}/move", json={"course_offering_id": other["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["course_offering_id"] == other["id"]