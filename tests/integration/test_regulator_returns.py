"""Regulator returns (generic summaries in the Reports centre): enrolment by program/semester/gender with the year's
movement, teaching staff with their term's courses, and per-program results for a term; each downloads as CSV."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.academics.models import CourseOffering, Enrollment, EnrollmentResult
from app.core.clock import institute_today
from app.core.config import settings
from tests.integration.test_attendance_and_grading import _login_admin
from tests.integration.test_result_sheets import _class

pytestmark = pytest.mark.asyncio
R = "/api/v1/report-center"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


async def test_three_summaries(app_client, admin_user, db_session):
    offering, students, _a, _teacher = await _class(app_client, db_session, code="REG1", n=3)
    off = await db_session.get(CourseOffering, uuid.UUID(offering))
    term = off.term_label
    enrollments = (await db_session.execute(select(Enrollment).where(Enrollment.course_offering_id == off.id))).scalars().all()
    for k, e in enumerate(enrollments):                    # two pass, one fails
        passed = k < 2
        db_session.add(EnrollmentResult(enrollment_id=e.id, percentage=70 if passed else 30, total_obtained=70 if passed else 30, total_max=100,
                                        letter_grade="B" if passed else "F", grade_points=3.0 if passed else 0.0, credit_hours=3,
                                        outcome="PASSED" if passed else "FAILED", computed_at=datetime.now(timezone.utc)))
    await db_session.commit()

    await _login_admin(app_client)
    cat = await _ok(await app_client.get(f"{R}/catalog"))
    group = next(g for g in cat["groups"] if g["name"] == "Regulator returns")
    assert [r["key"] for r in group["reports"]] == ["regulator.enrolment", "regulator.faculty", "regulator.results"]

    en = await _ok(await app_client.get(f"{R}/run/regulator.enrolment", params={"year": institute_today().year}))
    assert en["totals"]["total"] >= 3 and en["totals"]["added"] >= 3
    assert en["totals"]["total"] == en["totals"]["male"] + en["totals"]["female"] + en["totals"]["other"]

    fa = await _ok(await app_client.get(f"{R}/run/regulator.faculty", params={"term": term}))
    teacher_row = next(r for r in fa["rows"] if off.course_id and r["credit_hours"] > 0)
    assert teacher_row["courses"] and fa["subtitle"] == term

    assert (await app_client.get(f"{R}/run/regulator.results")).status_code == 422                     # the term is needed
    res = await _ok(await app_client.get(f"{R}/run/regulator.results", params={"term": term}))
    assert res["totals"]["appeared"] == 3 and res["totals"]["passed"] == 2 and res["totals"]["failed"] == 1
    assert res["totals"]["pass_pct"] == 66.7 and res["rows"][0]["avg_gpa"] == 2.0

    csv = await app_client.get(f"{R}/export/regulator.results", params={"term": term})
    assert csv.status_code == 200 and "Results summary" in csv.text and "Passed every course" in csv.text
