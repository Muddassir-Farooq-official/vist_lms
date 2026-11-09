"""Student home: the Today panel and the latest-marks it uses."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.academics.models import AssessmentType, OfferingAssessment, StudentMark
from tests.integration.test_attendance_and_grading import _login, _setup_offering_with_enrolled_students

pytestmark = pytest.mark.asyncio


async def test_latest_marks_are_the_students_own_newest_first(app_client, admin_user, db_session):
    off, studs = await _setup_offering_with_enrolled_students(app_client, code="SH1", n_students=2, cnic_base=8300)
    (_, me, my_enr), (_, _other, other_enr) = studs
    now = datetime.now(timezone.utc)
    for i, (name, got, mine) in enumerate((("Quiz 1", 4, True), ("Quiz 2", 9, True), ("Quiz 3", 1, False))):
        t = AssessmentType(name=f"SH {name}", max_marks=10)
        db_session.add(t)
        await db_session.flush()
        oa = OfferingAssessment(course_offering_id=uuid.UUID(off), assessment_type_id=t.id)
        db_session.add(oa)
        await db_session.flush()
        db_session.add(StudentMark(enrollment_id=uuid.UUID(my_enr if mine else other_enr), offering_assessment_id=oa.id, obtained_marks=got,
                                   entered_at=now - timedelta(hours=10 - i)))
    await db_session.commit()

    await _login(app_client, me, "StudentPass123!")
    rows = (await app_client.get("/api/v1/academics/enrollments/me/recent-marks", params={"limit": 4})).json()
    assert [(r["assessment"], r["obtained"], r["percentage"]) for r in rows] == [("SH Quiz 2", 9.0, 90.0), ("SH Quiz 1", 4.0, 40.0)]
    assert rows[0]["course_code"] == "SH1-C1"


async def test_the_home_page_has_the_today_panel(app_client):
    html = (await app_client.get("/dashboard/student", headers={"accept": "text/html"})).text
    for needle in ('id="today-classes"', 'id="today-due"', 'id="today-marks"', "student-home.css", "kpi-row--compact"):
        assert needle in html, needle
    js = (await app_client.get("/static/js/student-home.js")).text
    assert "recent-marks" in js and "/api/v1/fees/accounts/me" in js and 'aria-label="Add to calendar"' in js
