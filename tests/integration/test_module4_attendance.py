"""Module 4 (Class attendance) launch-readiness tests."""
from __future__ import annotations

import pytest

from app.employees.models import JobRole
from tests.integration.test_attendance_and_grading import (
    ADMIN,
    _create_n_sessions,
    _login,
    _login_admin,
    _setup_offering_with_enrolled_students,
)

pytestmark = pytest.mark.asyncio

NOTE = "backfilled test data"


@pytest.fixture(autouse=True)
def _no_login_throttle(monkeypatch):
    """These multi-actor flows sign in as admin/teacher/student many times in
    a few seconds; the login rate limiter (covered by its own tests) would
    otherwise trip on the shared test IP."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _mark(client, session_id, present=(), note=NOTE):
    """Attendance has two options: students in `present` are Present, every other enrolled student is Absent."""
    return await client.post(
        f"/api/v1/attendance/sessions/{session_id}/mark",
        json={"present_student_ids": list(present), "late_note": note},
    )


async def _pct(client, offering_id, student_id):
    r = await client.get(f"/api/v1/attendance/offerings/{offering_id}/students/{student_id}/percentage")
    assert r.status_code == 200, r.text
    return r.json()


async def _teacher_assigned(client, db_session, offering_id, code="ATCH", cnic="35202-9100001-1"):
    jr = JobRole(code=code, title="Teacher", default_role_code="TEACHER")
    db_session.add(jr)
    await db_session.commit()
    r = await client.post("/api/v1/employees", json={
        "first_name": code, "last_name": "T", "date_of_birth": "1985-01-01", "gender": "Male", "cnic": cnic,
        "contact_no": "03007770000", "hire_date": "2026-01-01", "job_role_id": str(jr.id), "base_salary": 1,
        "password": "TeacherPass123!"})
    assert r.status_code == 201, r.text
    emp = r.json()
    if offering_id:
        p = await client.patch(f"/api/v1/academics/offerings/{offering_id}", json={"assigned_teacher_id": emp["id"]})
        assert p.status_code == 200, p.text
    return emp["username"]


async def _teacher_login(client, username):
    await _login(client, username, "TeacherPass123!")
    ch = await client.post("/api/v1/auth/change-password",
                           json={"current_password": "TeacherPass123!", "new_password": "TeacherNew12345!"})
    assert ch.status_code == 204, ch.text
    await _login(client, username, "TeacherNew12345!")


async def test_attendance_is_only_present_or_absent(app_client, admin_user, db_session):
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="M4A", n_students=3)
    s1, s2, s3 = (s[0] for s in studs)
    sess = await _create_n_sessions(app_client, offering_id, 4)
    await _mark(app_client, sess[0], present=[s1, s2])           # s3 absent
    await _mark(app_client, sess[1], present=[s1, s2, s3])
    await _mark(app_client, sess[2], present=[s1])               # s2, s3 absent
    await _mark(app_client, sess[3], present=[s1])
    p1 = await _pct(app_client, offering_id, s1)
    p2 = await _pct(app_client, offering_id, s2)
    p3 = await _pct(app_client, offering_id, s3)
    assert (p1["present_count"], p1["total_sessions_taken"], p1["percentage"], p1["absent_count"]) == (4, 4, 100.0, 0)
    assert (p2["present_count"], p2["total_sessions_taken"], p2["percentage"], p2["absent_count"]) == (2, 4, 50.0, 2)
    assert (p3["present_count"], p3["total_sessions_taken"], p3["percentage"], p3["absent_count"]) == (1, 4, 25.0, 3)
    assert "late_count" not in p1 and "leave_count" not in p1          # the concepts do not exist


async def test_late_and_leave_are_not_accepted_or_stored(app_client, admin_user, db_session):
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="M4B", n_students=2)
    s1, s2 = studs[0][0], studs[1][0]
    sess = await _create_n_sessions(app_client, offering_id, 1)
    # even if a client sends the old fields, they change nothing: not-present means Absent
    r = await app_client.post(f"/api/v1/attendance/sessions/{sess[0]}/mark",
                              json={"present_student_ids": [s1], "late_student_ids": [s2], "leave_student_ids": [s2], "late_note": NOTE})
    assert r.status_code == 200, r.text
    statuses = {x["student_id"]: x["status"] for x in (await app_client.get(f"/api/v1/attendance/sessions/{sess[0]}")).json()}
    assert statuses == {s1: "PRESENT", s2: "ABSENT"}
    from app.attendance.models import AttendanceStatus
    # The register is two-way. LEAVE exists only for approved (e.g. medical) leave recorded by the office.
    assert {m.value for m in AttendanceStatus} == {"PRESENT", "ABSENT", "LEAVE"}


async def test_register_rejects_students_who_are_not_enrolled(app_client, admin_user, db_session):
    offering_id, _ = await _setup_offering_with_enrolled_students(app_client, code="M4B2", n_students=1)
    sess = await _create_n_sessions(app_client, offering_id, 1)
    bad = await _mark(app_client, sess[0], present=["00000000-0000-0000-0000-000000000000"])
    assert bad.status_code == 422 and "not currently enrolled" in bad.text


async def test_cancelled_class_does_not_count_for_or_against_anyone(app_client, admin_user, db_session):
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="M4C", n_students=1)
    sid = studs[0][0]
    sess = await _create_n_sessions(app_client, offering_id, 3)
    await _mark(app_client, sess[0], present=[sid])
    await _mark(app_client, sess[1], present=[sid])
    await _mark(app_client, sess[2], present=[])  # absent
    assert (await _pct(app_client, offering_id, sid))["percentage"] == 66.67
    assert (await app_client.post(f"/api/v1/academics/class-sessions/{sess[2]}/cancel", json={"reason": "Marked by mistake"})).status_code == 200
    assert (await _pct(app_client, offering_id, sid))["percentage"] == 100.0


async def test_class_with_no_students_can_be_cleared_from_needs_attention(app_client, admin_user, db_session):
    """Regression: a class with zero enrolled students can never have attendance
    rows, so it used to stay on the needs-attention list forever."""
    offering_id, _ = await _setup_offering_with_enrolled_students(app_client, code="M4D", n_students=0)
    sess = await _create_n_sessions(app_client, offering_id, 2)
    listed = [i["id"] for i in (await app_client.get("/api/v1/attendance/needs-attention")).json()]
    assert sess[0] in listed and sess[1] in listed
    assert (await _mark(app_client, sess[0])).status_code == 200
    listed = [i["id"] for i in (await app_client.get("/api/v1/attendance/needs-attention")).json()]
    assert sess[0] not in listed and sess[1] in listed


async def test_lock_is_refused_while_held_classes_are_unrecorded_and_preview_explains(app_client, admin_user, db_session):
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="M4E", n_students=1)
    sid = studs[0][0]
    sess = await _create_n_sessions(app_client, offering_id, 2)
    await _mark(app_client, sess[0], present=[sid])
    pv = (await app_client.get(f"/api/v1/attendance/offerings/{offering_id}/lock-preview")).json()
    assert pv["can_lock"] is False and pv["unmarked_overdue_sessions"] >= 1 and pv["enrolled_count"] == 1
    refused = await app_client.post(f"/api/v1/attendance/offerings/{offering_id}/lock")
    assert refused.status_code == 422 and "no attendance recorded" in refused.text
    # resolve: mark the rest / cancel; then it locks
    pending = [i for i in (await app_client.get("/api/v1/attendance/needs-attention")).json() if i["course_offering_id"] == offering_id]
    for item in pending:
        assert (await app_client.post(f"/api/v1/academics/class-sessions/{item['id']}/cancel", json={"reason": "holiday"})).status_code == 200
    pv2 = (await app_client.get(f"/api/v1/attendance/offerings/{offering_id}/lock-preview")).json()
    assert pv2["can_lock"] is True and pv2["unmarked_overdue_sessions"] == 0
    assert (await app_client.post(f"/api/v1/attendance/offerings/{offering_id}/lock")).status_code == 200
    assert (await app_client.get(f"/api/v1/attendance/offerings/{offering_id}/lock-preview")).json()["already_locked"] is True


async def test_lock_preview_counts_who_would_be_dropped_or_fined(app_client, admin_user, db_session, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "attendance_fine_mode", 0)          # the older rule: one fine per term at lock, automatic drop
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="M4F", n_students=2)
    good, bad = studs[0][0], studs[1][0]
    sess = await _create_n_sessions(app_client, offering_id, 2)
    await _mark(app_client, sess[0], present=[good])
    await _mark(app_client, sess[1], present=[good])   # `bad` absent twice -> 0%
    pv = (await app_client.get(f"/api/v1/attendance/offerings/{offering_id}/lock-preview")).json()
    assert pv["would_drop"] == 1 and pv["would_fine"] == 0 and pv["below_eligibility"] == 1


async def test_teacher_correction_request_approve_and_reject_flow(app_client, admin_user, db_session):
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="M4G", n_students=2)
    s1, s2 = studs[0][0], studs[1][0]
    sess = await _create_n_sessions(app_client, offering_id, 2)
    username = await _teacher_assigned(app_client, db_session, offering_id)
    other = await _teacher_assigned(app_client, db_session, None, code="OTHR", cnic="35202-9100002-2")

    await _teacher_login(app_client, username)
    # window is long closed: the teacher cannot mark directly...
    direct = await _mark(app_client, sess[0], present=[s1])
    assert direct.status_code == 422 and "window" in direct.text
    # ...so they ask the admin
    body = {"reason": "Forgot to submit, was on stage duty", "present_student_ids": [s1]}
    req = await app_client.post(f"/api/v1/attendance/sessions/{sess[0]}/correction-request", json=body)
    assert req.status_code == 201, req.text
    rid = req.json()["id"]
    assert req.json()["status"] == "PENDING" and req.json()["present_count"] == 1
    assert (await app_client.post(f"/api/v1/attendance/sessions/{sess[0]}/correction-request", json=body)).status_code == 422  # duplicate
    mine = (await app_client.get("/api/v1/attendance/correction-requests")).json()
    assert [r["id"] for r in mine] == [rid]
    # a teacher cannot approve their own request
    assert (await app_client.post(f"/api/v1/attendance/correction-requests/{rid}/decision", json={"approve": True})).status_code == 403
    # another (unassigned) teacher cannot request for this class, and sees nothing
    await _teacher_login(app_client, other)
    assert (await app_client.post(f"/api/v1/attendance/sessions/{sess[0]}/correction-request", json=body)).status_code == 403
    assert (await app_client.get("/api/v1/attendance/correction-requests")).json() == []

    # admin approves -> register applied
    await _login_admin(app_client)
    pending = (await app_client.get("/api/v1/attendance/correction-requests", params={"status": "pending"})).json()
    assert len(pending) == 1
    ok = await app_client.post(f"/api/v1/attendance/correction-requests/{rid}/decision", json={"approve": True, "note": "ok"})
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
    sheet = {r["student_id"]: r["status"] for r in (await app_client.get(f"/api/v1/attendance/sessions/{sess[0]}")).json()}
    assert sheet == {s1: "PRESENT", s2: "ABSENT"}
    assert (await app_client.post(f"/api/v1/attendance/correction-requests/{rid}/decision", json={"approve": False})).status_code == 422  # already decided
    assert sess[0] not in [i["id"] for i in (await app_client.get("/api/v1/attendance/needs-attention")).json()]

    # reject path leaves the class unrecorded
    await _login(app_client, username, "TeacherNew12345!")  # password was already changed above
    r2 = await app_client.post(f"/api/v1/attendance/sessions/{sess[1]}/correction-request", json={**body, "reason": "Second one please"})
    assert r2.status_code == 201, r2.text
    await _login_admin(app_client)
    no = await app_client.post(f"/api/v1/attendance/correction-requests/{r2.json()['id']}/decision", json={"approve": False, "note": "No evidence"})
    assert no.status_code == 200 and no.json()["status"] == "REJECTED"
    assert (await app_client.get(f"/api/v1/attendance/sessions/{sess[1]}")).json() == []


async def test_correction_request_validation(app_client, admin_user, db_session):
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="M4H", n_students=1)
    sid = studs[0][0]
    sess = await _create_n_sessions(app_client, offering_id, 1)
    username = await _teacher_assigned(app_client, db_session, offering_id)
    await _teacher_login(app_client, username)
    url = f"/api/v1/attendance/sessions/{sess[0]}/correction-request"
    assert (await app_client.post(url, json={"reason": "no", "present_student_ids": []})).status_code == 422
    assert (await app_client.post(url, json={"reason": "valid reason", "present_student_ids": ["00000000-0000-0000-0000-000000000000"]})).status_code == 422
    await _login_admin(app_client)
    assert (await app_client.post(f"/api/v1/attendance/offerings/{offering_id}/lock")).status_code in (200, 422)


async def test_student_sees_own_class_by_class_history_only(app_client, admin_user, db_session):
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="M4I", n_students=2)
    (s1, u1, _), (s2, u2, _) = studs
    sess = await _create_n_sessions(app_client, offering_id, 2)
    await _mark(app_client, sess[0], present=[s1])
    await _mark(app_client, sess[1], present=[s1])
    await _login(app_client, u1, "StudentPass123!")
    mine = (await app_client.get(f"/api/v1/attendance/me/offerings/{offering_id}/records")).json()
    assert [r["status"] for r in mine] == ["PRESENT", "PRESENT"] and mine[0]["session_date"] < mine[1]["session_date"]
    await _login(app_client, u2, "StudentPass123!")
    theirs = (await app_client.get(f"/api/v1/attendance/me/offerings/{offering_id}/records")).json()
    assert [r["status"] for r in theirs] == ["ABSENT", "ABSENT"]
    # students cannot use staff endpoints
    for method, url in [("get", f"/api/v1/attendance/offerings/{offering_id}/lock-preview"),
                        ("get", f"/api/v1/attendance/offerings/{offering_id}/report.csv"),
                        ("get", "/api/v1/attendance/needs-attention")]:
        assert (await getattr(app_client, method)(url)).status_code == 403, url
    assert (await app_client.post(f"/api/v1/attendance/sessions/{sess[0]}/correction-request",
                                  json={"reason": "please let me", "present_student_ids": []})).status_code == 403


async def test_register_csv_export(app_client, admin_user, db_session):
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="M4J", n_students=2)
    s1, s2 = studs[0][0], studs[1][0]
    sess = await _create_n_sessions(app_client, offering_id, 2)
    await _mark(app_client, sess[0], present=[s1])
    await _mark(app_client, sess[1], present=[s1])
    r = await app_client.get(f"/api/v1/attendance/offerings/{offering_id}/report.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.content.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("roll_number,name,") and lines[0].endswith("attended,counted,percentage,absent")
    row1 = next(line for line in lines if ",P,P," in line)
    assert row1.endswith(",2,2,100.0,0")
    row2 = next(line for line in lines if ",A,A," in line)
    assert row2.endswith(",0,2,0.0,2")
    assert "P=present,A=absent" in lines[-1]
    # an unassigned teacher may not download someone else's register
    other = await _teacher_assigned(app_client, db_session, None, code="OTH2", cnic="35202-9100003-3")
    await _teacher_login(app_client, other)
    assert (await app_client.get(f"/api/v1/attendance/offerings/{offering_id}/report.csv")).status_code == 403


async def test_cancel_whole_day_skips_classes_already_taken(app_client, admin_user, db_session):
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code="M4K", n_students=1)
    sid = studs[0][0]
    sess = await _create_n_sessions(app_client, offering_id, 3)
    sessions = (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/class-sessions")).json()
    by_id = {s["id"]: s for s in sessions}
    await _mark(app_client, sess[0], present=[sid])
    taken_date, holiday = by_id[sess[0]]["session_date"], by_id[sess[1]]["session_date"]

    r = await app_client.post("/api/v1/academics/class-sessions/cancel-day", json={"session_date": holiday, "reason": "Public holiday", "term_label": "fall 2026"})
    assert r.status_code == 200 and r.json()["cancelled"] == 1
    again = await app_client.post("/api/v1/academics/class-sessions/cancel-day", json={"session_date": taken_date, "reason": "Strike"})
    assert again.json() == {"date": taken_date, "cancelled": 0, "skipped_attendance_already_taken": 1}
    statuses = {s["id"]: s["status"] for s in (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/class-sessions")).json()}
    assert statuses[sess[1]] == "CANCELLED" and statuses[sess[0]] == "SCHEDULED" and statuses[sess[2]] == "SCHEDULED"


async def test_config_exposes_server_clock_and_rules(app_client, admin_user, db_session):
    await _login_admin(app_client)
    cfg = (await app_client.get("/api/v1/attendance/config")).json()
    assert cfg["marking_window_minutes"] == 15 and "T" in cfg["server_now"]
    assert cfg["eligibility_threshold"] == 75.0 and cfg["drop_threshold"] == 40.0