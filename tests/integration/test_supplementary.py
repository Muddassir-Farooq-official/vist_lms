"""Supplementary (reappear) exams: only a published, marks-based fail can sit one; the paper replaces the final
component; a pass replaces the failed result (and can be grade-capped), a fail or absence leaves it as it was."""
from __future__ import annotations

import pytest

from app.core.config import settings
from tests.integration.test_attendance_and_grading import _login, _login_admin
from tests.integration.test_exam_controller import _exam_user
from tests.integration.test_result_sheets import _as_teacher, _class, _fill

pytestmark = pytest.mark.asyncio
R = "/api/v1/results"
S = "/api/v1/supplementary"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _published(client, db, code):
    """3 students, Quiz /20 + Final /100: scores 18+70 (pass), 10+20 = 25% (FAIL), 15+60 (pass). Published."""
    offering, students, assessments, teacher = await _class(client, db, code=code, cnic_base=abs(hash(code)) % 9000 + 1000)
    await _as_teacher(client, teacher)
    await _fill(client, students, assessments)
    assert (await client.post(f"{R}/offerings/{offering}/submit")).status_code == 200
    await _login_admin(client)
    assert (await client.post(f"{R}/sheets/{offering}/approve")).status_code == 200
    assert (await client.post(f"{R}/sheets/{offering}/publish")).status_code == 200
    term = (await client.get(f"/api/v1/academics/offerings/{offering}")).json()["term_label"]
    return term, students


async def _register_failer(client, term):
    elig = (await client.get(f"{S}/eligible", params={"term_label": term})).json()
    assert len(elig) == 1 and elig[0]["percentage"] == 25.0
    assert (await client.post(f"{S}/register", json={"enrollment_ids": [elig[0]["enrollment_id"]]})).status_code == 201
    att = (await client.get(f"{S}/attempts", params={"term_label": term})).json()
    assert att[0]["component_max"] == 100 and att[0]["status"] == "REGISTERED"
    return elig[0], att[0]["id"]


async def test_supplementary_pass_replaces_the_failed_result(app_client, admin_user, db_session):
    term, students = await _published(app_client, db_session, "SUP1")
    row, aid = await _register_failer(app_client, term)
    assert (await app_client.get(f"{S}/eligible", params={"term_label": term})).json() == []     # one attempt per failed result
    too_high = await app_client.post(f"{S}/attempts/{aid}/mark", json={"obtained_marks": 101})
    assert too_high.status_code == 422
    m = (await app_client.post(f"{S}/attempts/{aid}/mark", json={"obtained_marks": 80})).json()
    assert m["preview"]["percentage"] == 75.0 and m["preview"]["outcome"] == "PASSED"           # (10 + 80) / 120
    # student cannot see the mark before publishing
    _sid, username, _e = students[1]
    await _login(app_client, username, "StudentPass123!")
    mine = (await app_client.get(f"{S}/me")).json()
    assert mine[0]["status"] == "MARKED" and mine[0]["obtained_marks"] is None
    await _login_admin(app_client)
    assert (await app_client.post(f"{S}/publish", json={"attempt_ids": [aid]})).json() == {"published": 1, "passed": 1, "not_passed": 0}
    t = (await app_client.get(f"{R}/students/{row['student_id']}/transcript")).json()
    course = next(c for term_ in t["terms"] for c in term_["courses"] if c["course_code"] == row["course_code"])
    assert course["outcome"] == "PASSED" and course["supplementary"] is True and course["percentage"] == 75.0
    assert t["summary"]["courses_to_clear"] == 0
    again = await app_client.post(f"{S}/attempts/{aid}/mark", json={"obtained_marks": 90})
    assert again.status_code == 422 and "already published" in again.text


async def test_failing_again_or_absent_keeps_the_original_result(app_client, admin_user, db_session):
    term, _students = await _published(app_client, db_session, "SUP2")
    row, aid = await _register_failer(app_client, term)
    await app_client.post(f"{S}/attempts/{aid}/mark", json={"absent": True})
    assert (await app_client.post(f"{S}/publish", json={"attempt_ids": [aid]})).json()["not_passed"] == 1
    t = (await app_client.get(f"{R}/students/{row['student_id']}/transcript")).json()
    course = next(c for term_ in t["terms"] for c in term_["courses"] if c["course_code"] == row["course_code"])
    assert course["outcome"] == "FAILED" and course["percentage"] == 25.0 and course["supplementary"] is False


async def test_grade_cap_and_publish_needs_marks(app_client, admin_user, db_session, monkeypatch):
    term, _students = await _published(app_client, db_session, "SUP3")
    row, aid = await _register_failer(app_client, term)
    early = await app_client.post(f"{S}/publish", json={"attempt_ids": [aid]})
    assert early.status_code == 422 and "Enter every mark" in early.text
    monkeypatch.setattr(settings, "supplementary_max_grade_points", 2.0)
    await app_client.post(f"{S}/attempts/{aid}/mark", json={"obtained_marks": 100})                 # 110/120 = 91.67%: an A, capped
    await app_client.post(f"{S}/publish", json={"attempt_ids": [aid]})
    t = (await app_client.get(f"{R}/students/{row['student_id']}/transcript")).json()
    course = next(c for term_ in t["terms"] for c in term_["courses"] if c["course_code"] == row["course_code"])
    assert course["outcome"] == "PASSED" and course["grade_points"] <= 2.0


async def test_attendance_fails_and_passes_cannot_register_and_exam_controller_runs_it(app_client, admin_user, db_session):
    term, students = await _published(app_client, db_session, "SUP4")
    passer_enrollment = students[0][2]
    r = await app_client.post(f"{S}/register", json={"enrollment_ids": [passer_enrollment]})
    assert r.status_code == 422 and "failed result" in r.text
    user, pw = await _exam_user(app_client, db_session)
    await _login(app_client, user, pw)
    assert len((await app_client.get(f"{S}/eligible", params={"term_label": term})).json()) == 1
    _sid, username, _e = students[0]
    await _login(app_client, username, "StudentPass123!")
    assert (await app_client.get(f"{S}/eligible", params={"term_label": term})).status_code == 403


async def test_a_fail_for_low_attendance_cannot_take_a_supplementary(app_client, admin_user, db_session):
    from sqlalchemy import select

    from app.academics.models import EnrollmentResult

    term, students = await _published(app_client, db_session, "SUP5")
    failer = students[1][2]
    res = (await db_session.execute(select(EnrollmentResult).where(EnrollmentResult.enrollment_id == failer))).scalar_one()
    res.failed_for_attendance = True
    await db_session.flush()
    assert (await app_client.get(f"{S}/eligible", params={"term_label": term})).json() == []
    r = await app_client.post(f"{S}/register", json={"enrollment_ids": [failer]})
    assert r.status_code == 422 and "attendance" in r.text