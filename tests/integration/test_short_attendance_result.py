"""Short attendance no longer shows a student as "Failed" while the term runs: the projection shows the grade the marks
earn and "Short attendance" (barred from the final exam); a published result says Short attendance (failed, repeat)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.academics.models import EnrollmentResult
from tests.integration.test_attendance_and_grading import (
    _create_n_sessions,
    _get_or_create_assessment_type,
    _publish_results,
    _setup_offering_with_enrolled_students,
)

pytestmark = pytest.mark.asyncio


async def test_short_attendance_shows_as_such_not_as_failed(app_client, admin_user, db_session):
    offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="SHORT1", n_students=2)
    (short_id, _u1, short_enr), (ok_id, _u2, ok_enr) = students
    for sid in await _create_n_sessions(app_client, offering_id, 2):
        await app_client.post(f"/api/v1/attendance/sessions/{sid}/mark", json={"present_student_ids": [ok_id], "late_note": "backfilled test data"})
    only = await _get_or_create_assessment_type(app_client, "Only Assessment", 100)
    a = (await app_client.post(f"/api/v1/academics/offerings/{offering_id}/assessments", json={"assessment_type_id": only})).json()["id"]
    await app_client.post(f"/api/v1/academics/enrollments/{short_enr}/marks/{a}", json={"obtained_marks": 95})
    await app_client.post(f"/api/v1/academics/enrollments/{ok_enr}/marks/{a}", json={"obtained_marks": 30})

    # the projection: the short student's grade comes from the marks; the result says Short attendance, not Failed
    await app_client.post(f"/api/v1/results/offerings/{offering_id}/submit")
    sheet = (await app_client.get(f"/api/v1/results/sheets/{offering_id}")).json()
    rows = {r["enrollment_id"]: r for r in sheet["rows"]}
    assert rows[short_enr]["outcome"] == "SHORT_ATTENDANCE" and rows[short_enr]["grade"] == "A" and rows[short_enr]["attendance_eligible"] is False
    assert rows[ok_enr]["outcome"] == "FAILED"                              # low marks still fail as before
    assert sheet["stats"]["short_attendance"] == 1 and sheet["stats"]["passed"] == 0 and sheet["stats"]["failed"] == 2

    # published: still a failed subject on record (repeat), shown as Short attendance
    for step in (f"/api/v1/results/sheets/{offering_id}/approve", f"/api/v1/results/sheets/{offering_id}/publish"):
        assert (await app_client.post(step)).status_code == 200
    done = {r["enrollment_id"]: r for r in (await app_client.get(f"/api/v1/results/sheets/{offering_id}")).json()["rows"]}
    assert done[short_enr]["outcome"] == "SHORT_ATTENDANCE"
    frozen = (await db_session.execute(select(EnrollmentResult).where(EnrollmentResult.failed_for_attendance.is_(True)))).scalars().all()
    assert len(frozen) == 1 and frozen[0].outcome == "FAILED"
    _ = (short_id, _publish_results)
