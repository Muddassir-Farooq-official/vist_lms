"""Step 2 launch guards, run with the PRODUCTION defaults (marker launch_guards):

1. Results cannot be published before the course's attendance is locked
   (admin-only override with a written reason, audited).
2. Attendance cannot be locked while classes remain / before term end
   (admin-only early lock with a written reason, audited).
3. An attendance fine no longer disables the student's login; it holds
   self-registration and the result card until the fine is paid.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.audit.models import AuditLog
from app.core.clock import institute_today
from app.core.config import settings
from tests.integration.test_attendance_and_grading import (
    _cancel_unmarked_sessions,
    _create_n_sessions,
    _get_or_create_period,
    _login,
    _login_admin,
    _setup_offering_with_enrolled_students,
)
from tests.integration.test_exam_controller import _exam_user
from tests.integration.test_result_sheets import _as_teacher, _class, _fill

pytestmark = [pytest.mark.asyncio, pytest.mark.launch_guards]
R = "/api/v1/results"
SP = "StudentPass123!"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(settings, "require_registered_terms", False)   # these tests are about other guards; terms are tested in test_academic_terms.py


async def _approved_sheet(client, db, code):
    """A course whose marks are entered, submitted and approved; attendance NOT locked."""
    offering, students, assessments, teacher = await _class(client, db, code=code, cnic_base=abs(hash(code)) % 9000 + 1000)
    await _as_teacher(client, teacher)
    await _fill(client, students, assessments)
    assert (await client.post(f"{R}/offerings/{offering}/submit")).status_code == 200
    await _login_admin(client)
    assert (await client.post(f"{R}/sheets/{offering}/approve")).status_code == 200
    return offering, students


async def _audit(db, action):
    return (await db.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all()


# ------------------------------------------------------------------ 1. publish guard
async def test_publish_is_refused_until_attendance_is_locked(app_client, admin_user, db_session):
    offering, _ = await _approved_sheet(app_client, db_session, "G2A")
    r = await app_client.post(f"{R}/sheets/{offering}/publish")
    assert r.status_code == 422 and "attendance is not locked" in r.text
    assert r.json()["error"]["details"]["override_allowed"] is True          # admin is told an override exists
    # publish-all skips it and says why
    bulk = (await app_client.post(f"{R}/publish-approved")).json()
    assert bulk["published"] == 0 and "attendance is not locked" in bulk["failed"][0]["reason"]
    # lock attendance (the test term is in the past, so this is not early), then publish works
    await _cancel_unmarked_sessions(app_client, offering)
    assert (await app_client.post(f"/api/v1/attendance/offerings/{offering}/lock")).status_code == 200
    ok = await app_client.post(f"{R}/sheets/{offering}/publish")
    assert ok.status_code == 200 and ok.json()["status"] == "PUBLISHED"


async def test_admin_can_publish_early_only_with_a_reason_and_it_is_audited(app_client, admin_user, db_session):
    offering, _ = await _approved_sheet(app_client, db_session, "G2B")
    short = await app_client.post(f"{R}/sheets/{offering}/publish", json={"override_reason": "because"})
    assert short.status_code == 422 and "10 characters" in short.text
    ok = await app_client.post(f"{R}/sheets/{offering}/publish", json={"override_reason": "Supplementary exam result, no classes"})
    assert ok.status_code == 200 and ok.json()["status"] == "PUBLISHED"
    assert len(await _audit(db_session, "result_sheet.published_before_attendance_lock")) == 1


async def test_exam_controller_cannot_override_the_publish_guard(app_client, admin_user, db_session):
    offering, _ = await _approved_sheet(app_client, db_session, "G2C")
    _u, exam = await _exam_user(app_client, db_session)
    await _login(app_client, _u, exam)
    r = await app_client.post(f"{R}/sheets/{offering}/publish", json={"override_reason": "I would like to publish now"})
    assert r.status_code == 422 and "Only an administrator" in r.text


# ------------------------------------------------------------------ 2. early-lock guard
async def _running_offering(client, code):
    """An offering whose term started 3 weeks ago and ends in 8 weeks, with a weekly class (future classes exist)."""
    await _login_admin(client)
    today = institute_today()
    program = (await client.post("/api/v1/programs", json={"code": code, "name": code, "duration_years": 4, "total_semesters": 8})).json()["id"]
    course = (await client.post("/api/v1/academics/courses", json={"code": f"{code}-1", "title": "Running", "credit_hours": 3})).json()["id"]
    await client.post(f"/api/v1/academics/courses/{course}/programs", json={"program_id": program, "semester_number": 1})
    off = await client.post("/api/v1/academics/offerings", json={"course_id": course, "term_label": "Running Term",
                            "term_start_date": str(today - timedelta(weeks=3)), "term_end_date": str(today + timedelta(weeks=8))})
    assert off.status_code == 201, off.text
    oid = off.json()["id"]
    period = await _get_or_create_period(client, "08:00:00", "08:50:00")
    day = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"][today.weekday()]
    slot = await client.post(f"/api/v1/academics/offerings/{oid}/timetable-slots", json={"day_of_week": day, "period_id": period, "room": f"R-{code}"})
    assert slot.status_code == 201, slot.text
    return oid


async def test_attendance_cannot_be_locked_mid_term(app_client, admin_user, db_session):
    oid = await _running_offering(app_client, "G2D")
    await _cancel_unmarked_sessions(app_client, oid)
    preview = (await app_client.get(f"/api/v1/attendance/offerings/{oid}/lock-preview")).json()
    assert preview["can_lock"] is False and "still to come" in preview["early_reason"]
    r = await app_client.post(f"/api/v1/attendance/offerings/{oid}/lock")
    assert r.status_code == 422 and "Too early to lock" in r.text and r.json()["error"]["details"]["override_allowed"] is True
    ok = await app_client.post(f"/api/v1/attendance/offerings/{oid}/lock", json={"early_reason": "Term cut short by flood closure"})
    assert ok.status_code == 200, ok.text
    assert len(await _audit(db_session, "attendance.locked_early")) == 1


# ------------------------------------------------------------------ 3. fine holds instead of login block
async def test_attendance_fine_keeps_login_but_holds_registration_and_result_card(app_client, admin_user, db_session):
    offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="G2E", n_students=1, cnic_base=6100)
    student_id, username, _enr = students[0]
    sessions = await _create_n_sessions(app_client, offering_id, 5)
    for i, sid in enumerate(sessions):                                       # 3 of 5 = 60%: fined, not dropped
        await app_client.post(f"/api/v1/attendance/sessions/{sid}/mark", json={"present_student_ids": [student_id] if i < 3 else [], "late_note": "test data"})
    await _cancel_unmarked_sessions(app_client, offering_id)
    lock = await app_client.post(f"/api/v1/attendance/offerings/{offering_id}/lock")
    assert lock.status_code == 200 and lock.json()["fined_count"] == 1

    # login still works
    await _login(app_client, username, SP)
    assert (await app_client.get("/api/v1/fines/me")).status_code == 200
    held = await app_client.get(f"{R}/me/transcript")
    assert held.status_code == 422 and "on hold" in held.text and held.json()["error"]["details"]["hold"] == "ATTENDANCE_FINE"
    assert (await app_client.get(f"{R}/me/transcript.pdf")).status_code == 422
    # a new open course to try to register for
    await _login_admin(app_client)
    other = await _running_offering(app_client, "G2F")
    await app_client.patch(f"/api/v1/academics/offerings/{other}", json={"is_open_for_enrollment": True})
    await _login(app_client, username, SP)
    reg = await app_client.post("/api/v1/academics/enrollments/me", json={"course_offering_id": other})
    assert reg.status_code == 422 and "Course registration is on hold" in reg.text

    # pay the fine -> holds lift
    await _login_admin(app_client)
    fine = next(f for f in (await app_client.get("/api/v1/fines")).json() if f["payer_id"] == student_id and f["source"] == "ATTENDANCE")
    assert (await app_client.post(f"/api/v1/fines/{fine['id']}/mark-paid", json={"note": "Paid"})).status_code == 200
    await _login(app_client, username, SP)
    assert (await app_client.get(f"{R}/me/transcript")).status_code == 200