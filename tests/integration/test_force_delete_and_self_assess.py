"""Force-deleting an exam type that subjects use (warning, typed name, CSV backup, published results untouched), and the
separate Self-assessments module (paper quiz marks; never part of results)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.academics.models import EnrollmentResult, ResultSheet, StudentMark
from app.core.config import settings
from tests.integration.test_attendance_and_grading import _login, _login_admin, _publish_results
from tests.integration.test_result_sheets import SP, _as_teacher, _class

pytestmark = pytest.mark.asyncio
A = "/api/v1/academics"
R = "/api/v1/results"
SA = "/api/v1/self-assessments"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _marks(client, students, quiz_oa, final_oa, q=15, f=70):
    for _sid, _u, e in students:
        assert (await client.post(f"{A}/enrollments/{e}/marks/{quiz_oa}", json={"obtained_marks": q})).status_code == 201
        assert (await client.post(f"{A}/enrollments/{e}/marks/{final_oa}", json={"obtained_marks": f})).status_code == 201


async def test_force_delete_an_exam_type_in_use(app_client, admin_user, db_session):
    pub, pub_students, (pq, pf), t1 = await _class(app_client, db_session, code="FD1", n=2, cnic_base=4600)
    await _login_admin(app_client)
    await _marks(app_client, pub_students, pq, pf)
    await _publish_results(app_client, pub)                                   # one class published
    frozen_before = {str(r.enrollment_id): (float(r.percentage), r.letter_grade) for r in
                     (await db_session.execute(select(EnrollmentResult))).scalars()}

    types = {t["name"]: t["id"] for t in (await app_client.get(f"{A}/assessment-types")).json()}
    quiz = types["FD1 Quiz"]
    plain = await app_client.delete(f"{A}/assessment-types/{quiz}")
    assert plain.status_code in (409, 422) and "using it" in plain.text
    usage = (await app_client.get(f"{A}/assessment-types/{quiz}/usage")).json()
    assert usage["subjects"] == 1 and usage["subjects_published"] == 1 and usage["marks"] == 2 and usage["students"] == 2

    backup = await app_client.get(f"{A}/assessment-types/{quiz}/marks.csv")
    assert backup.status_code == 200 and backup.text.count("\n") == 3 and "FD1 Quiz" in backup.text and "15.0" in backup.text
    wrong = await app_client.delete(f"{A}/assessment-types/{quiz}", params={"force": "true", "confirm_name": "Quiz"})
    assert wrong.status_code in (409, 422) and "Type the exam type's name" in wrong.text
    ok = await app_client.delete(f"{A}/assessment-types/{quiz}", params={"force": "true", "confirm_name": "fd1  quiz"})
    assert ok.status_code == 204, ok.text

    assert (await app_client.get(f"{A}/assessment-types/{quiz}/usage")).status_code == 404
    db_session.expire_all()
    assert (await db_session.execute(select(func.count()).select_from(StudentMark).where(
        StudentMark.offering_assessment_id == uuid.UUID(pq)))).scalar_one() == 0
    after = {str(r.enrollment_id): (float(r.percentage), r.letter_grade) for r in (await db_session.execute(select(EnrollmentResult))).scalars()}
    assert after == frozen_before                                            # published results untouched


async def test_force_delete_sends_a_submitted_result_back_and_only_admins_may_force(app_client, admin_user, db_session):
    off, students, (q, f), teacher = await _class(app_client, db_session, code="FD2", n=1, cnic_base=4700)
    await _login_admin(app_client)
    await _marks(app_client, students, q, f)
    await _as_teacher(app_client, teacher)
    assert (await app_client.post(f"{R}/offerings/{off}/submit")).status_code == 200
    await _login_admin(app_client)
    quiz = {t["name"]: t["id"] for t in (await app_client.get(f"{A}/assessment-types")).json()}["FD2 Quiz"]
    assert (await app_client.delete(f"{A}/assessment-types/{quiz}", params={"force": "true", "confirm_name": "FD2 Quiz"})).status_code == 204
    db_session.expire_all()
    sheet = (await db_session.execute(select(ResultSheet).where(ResultSheet.course_offering_id == uuid.UUID(off)))).scalar_one()
    assert sheet.status.value == "RETURNED" and "FD2 Quiz was deleted" in sheet.return_reason


async def test_self_assessments_are_marks_only_and_separate_from_results(app_client, admin_user, db_session):
    off, students, (q, f), teacher = await _class(app_client, db_session, code="SA1", n=3, cnic_base=4800)
    await _as_teacher(app_client, teacher)
    courses = (await app_client.get(f"{SA}/my-courses")).json()
    assert off in [c["id"] for c in courses]
    a = await app_client.post(f"{SA}/courses/{off}", json={"title": "Weekly quiz 1", "kind": "QUIZ", "held_on": "2026-11-10", "max_marks": 10, "topic": "Cell"})
    assert a.status_code == 201, a.text
    aid = a.json()["id"]
    e1, e2, e3 = (s[2] for s in students)
    over = await app_client.put(f"{SA}/{aid}/marks", json={"entries": [{"enrollment_id": e1, "obtained": 11}]})
    assert over.status_code in (409, 422)
    saved = await app_client.put(f"{SA}/{aid}/marks", json={"entries": [{"enrollment_id": e1, "obtained": 8, "remark": "Good"},
                                                                        {"enrollment_id": e2, "obtained": None, "absent": True}]})
    assert saved.status_code == 200 and saved.json()["saved"] == 2

    roll3 = None
    view = (await app_client.get(f"{SA}/courses/{off}")).json()
    for r in view["rows"]:
        if r["enrollment_id"] == e3:
            roll3 = r["roll_number"]
    bad = await app_client.post(f"{SA}/{aid}/marks.csv", files={"file": ("m.csv", f"roll_number,marks\n{roll3},4\nNOPE,5\n", "text/csv")})
    assert bad.status_code in (409, 422) and "NOPE" in bad.text
    good = await app_client.post(f"{SA}/{aid}/marks.csv", files={"file": ("m.csv", f"roll_number,marks\n{roll3},4\n", "text/csv")})
    assert good.status_code == 200 and good.json()["saved"] == 1
    view = (await app_client.get(f"{SA}/courses/{off}")).json()
    item = view["items"][0]
    assert item["entered"] == 2 and item["absent"] == 1 and item["average"] == 6.0 and item["under_half"] == 1
    assert (await app_client.put(f"{SA}/{aid}", json={"title": "Weekly quiz 1", "kind": "QUIZ", "held_on": "2026-11-10", "max_marks": 5})).status_code in (409, 422)

    # the student sees their mark and the class average; it is not in their results
    await _login(app_client, students[0][1], SP)
    mine = (await app_client.get(f"{SA}/me")).json()
    course = next(c for c in mine if c["course_offering_id"] == off)
    assert course["items"][0]["obtained"] == 8 and course["items"][0]["class_average"] == 6.0 and course["average_percent"] == 80.0
    assert (await app_client.get(f"{SA}/children/{students[0][0]}")).status_code == 404          # not a parent
    assert (await app_client.post(f"{SA}/courses/{off}", json={"title": "x", "kind": "QUIZ", "held_on": "2026-11-10", "max_marks": 10})).status_code == 403
    db_session.expire_all()
    assert (await db_session.execute(select(func.count()).select_from(StudentMark).where(StudentMark.enrollment_id == uuid.UUID(e1)))).scalar_one() == 0

    # delete
    await _as_teacher(app_client, teacher)
    assert (await app_client.delete(f"{SA}/{aid}")).status_code == 204
    assert (await app_client.get(f"{SA}/courses/{off}")).json()["items"] == []
    _ = q, f
