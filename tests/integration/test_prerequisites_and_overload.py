"""Course prerequisites (pass A before taking B) and the credit-hour limit, each with an administrator override."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from tests.integration.test_attendance_and_grading import TERM_END, TERM_START, _login, _setup_offering_with_enrolled_students

pytestmark = pytest.mark.asyncio
A = "/api/v1/academics"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _program_of(client, code):
    return next(p["id"] for p in (await client.get("/api/v1/programs")).json() if p["code"] == code)


async def _course_offering(client, program_id, code, credit_hours=3):
    c = await client.post(f"{A}/courses", json={"code": code, "title": f"Course {code}", "credit_hours": credit_hours})
    assert c.status_code == 201, c.text
    cid = c.json()["id"]
    assert (await client.post(f"{A}/courses/{cid}/programs", json={"program_id": program_id, "semester_number": 1})).status_code in (200, 201)
    o = await client.post(f"{A}/offerings", json={"course_id": cid, "term_label": "Fall 2026", "term_start_date": str(TERM_START), "term_end_date": str(TERM_END)})
    assert o.status_code == 201, o.text
    await client.patch(f"{A}/offerings/{o.json()['id']}", json={"is_open_for_enrollment": True})
    return cid, o.json()["id"]


async def test_a_prerequisite_blocks_the_student_and_an_administrator_can_enrol_with_a_reason(app_client, admin_user, db_session):
    from app.audit.models import AuditLog

    off1, [(sid, username, _e)] = await _setup_offering_with_enrolled_students(app_client, code="PRQ", n_students=1, cnic_base=7100)
    pid = await _program_of(app_client, "PRQ")
    c1 = (await app_client.get(f"{A}/offerings/{off1}")).json()["course_id"]
    c2, off2 = await _course_offering(app_client, pid, "PRQ-C2")
    r = await app_client.put(f"{A}/courses/{c2}/prerequisites", json={"requires_course_ids": [c1]})
    assert r.status_code == 200 and [x["code"] for x in r.json()] == ["PRQ-C1"]

    await _login(app_client, username, "StudentPass123!")
    mine = await app_client.post(f"{A}/enrollments/me", json={"course_offering_id": off2})
    assert mine.status_code == 422 and "needs PRQ-C1 passed first" in mine.text and "administrator can enrol" not in mine.text

    from tests.integration.test_attendance_and_grading import _login_admin

    await _login_admin(app_client)
    refused = await app_client.post(f"{A}/offerings/{off2}/enroll-student", json={"student_id": sid})
    assert refused.status_code == 422 and refused.json()["error"]["details"]["overridable"] == "PREREQUISITE"
    short = await app_client.post(f"{A}/offerings/{off2}/enroll-student", json={"student_id": sid, "override_reason": "ok"})
    assert short.status_code == 422 and "at least 10" in short.text
    ok = await app_client.post(f"{A}/offerings/{off2}/enroll-student", json={"student_id": sid, "override_reason": "Passed it at the previous college"})
    assert ok.status_code == 201, ok.text
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "enrollment.rule_overridden"))).scalars().all()
    assert any("Passed it at the previous college" in str(x.log_metadata or "") for x in log)


async def test_an_overload_above_the_credit_limit_needs_an_administrator_and_a_reason(app_client, admin_user, db_session):
    off1, [(sid, _u, _e)] = await _setup_offering_with_enrolled_students(app_client, code="OVL", n_students=1, cnic_base=7200)
    pid = await _program_of(app_client, "OVL")
    _c, big = await _course_offering(app_client, pid, "OVL-BIG", credit_hours=10)
    _c, big2 = await _course_offering(app_client, pid, "OVL-BIG2", credit_hours=10)
    assert (await app_client.post(f"{A}/offerings/{big}/enroll-student", json={"student_id": sid})).status_code == 201   # 3 + 10 = 13
    batch = await app_client.post(f"{A}/offerings/{big2}/enroll-batch", json={"student_ids": [sid]})
    assert batch.status_code == 200
    (f,) = batch.json()["failed"]
    assert f["overridable"] == "CREDIT_LIMIT" and f["student_id"] == sid and "limit" in f["reason"]          # 23 > 18
    ok = await app_client.post(f"{A}/offerings/{big2}/enroll-student", json={"student_id": sid, "override_reason": "Final semester, approved by HOD"})
    assert ok.status_code == 201, ok.text


async def test_prerequisites_cannot_go_in_a_circle_or_point_at_the_course_itself(app_client, admin_user, db_session):
    off1, _s = await _setup_offering_with_enrolled_students(app_client, code="CYC", n_students=0, cnic_base=7300)
    pid = await _program_of(app_client, "CYC")
    a = (await app_client.get(f"{A}/offerings/{off1}")).json()["course_id"]
    b, _o = await _course_offering(app_client, pid, "CYC-B")
    c, _o = await _course_offering(app_client, pid, "CYC-C")
    assert (await app_client.put(f"{A}/courses/{b}/prerequisites", json={"requires_course_ids": [a]})).status_code == 200
    assert (await app_client.put(f"{A}/courses/{c}/prerequisites", json={"requires_course_ids": [b]})).status_code == 200
    loop = await app_client.put(f"{A}/courses/{a}/prerequisites", json={"requires_course_ids": [c]})
    assert loop.status_code == 422 and "already requires" in loop.text
    itself = await app_client.put(f"{A}/courses/{a}/prerequisites", json={"requires_course_ids": [a]})
    assert itself.status_code == 422
    assert (await app_client.put(f"{A}/courses/{c}/prerequisites", json={"requires_course_ids": []})).json() == []


async def test_a_published_pass_meets_the_prerequisite(app_client, admin_user, db_session):
    from app.academics.models import CourseOffering
    from app.academics.prerequisites import PrerequisiteService, missing_for
    from tests.integration.test_launch_step4_promotion import _published_class

    students, _rolls = await _published_class(app_client, db_session, "PRP")             # student 0 passes, student 1 fails
    passed, failed = uuid.UUID(students[0][0]), uuid.UUID(students[1][0])
    from app.academics.models import Enrollment

    enr = (await db_session.execute(select(Enrollment).where(Enrollment.student_id == passed))).scalars().first()
    course_id = (await db_session.get(CourseOffering, enr.course_offering_id)).course_id
    nxt = await app_client.post(f"{A}/courses", json={"code": "PRP-NEXT", "title": "Next", "credit_hours": 3})
    nid = uuid.UUID(nxt.json()["id"])
    await PrerequisiteService(db_session).replace(nid, [course_id], actor_id=admin_user.id)
    assert await missing_for(db_session, passed, nid) == []
    assert [c.id for c in await missing_for(db_session, failed, nid)] == [course_id]
