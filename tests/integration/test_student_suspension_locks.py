"""A suspended or expelled student is locked out of everything new (owner decisions, 1.64.0): attendance shows them as
Suspended and does not count, marks entry is locked and their result is held (worked out when the suspension is lifted),
no admit card, no seat in the exam hall, no certificates (an expelled student still gets the leaving papers). Nothing that
was already recorded changes."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.academics.models import Enrollment
from app.attendance.models import AttendanceRecord
from tests.integration.test_attendance_and_grading import (
    _create_n_sessions,
    _get_or_create_assessment_type,
    _publish_results,
    _setup_offering_with_enrolled_students,
)
from tests.integration.test_exam_datesheets import E, _paper, _schedule, _term_of
from tests.integration.test_exam_datesheets import _quiet as _quiet  # noqa: F401  (rate limits off)

pytestmark = pytest.mark.asyncio
LATE = "backfilled test data"


async def _status(client, sid, status, reason="Disciplinary committee decision"):
    r = await client.post(f"/api/v1/students/{sid}/status", json={"new_status": status, "reason": reason})
    assert r.status_code == 200, r.text


async def test_suspended_student_is_shown_locked_and_their_classes_do_not_count(app_client, admin_user, db_session):
    offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="SUSA", n_students=2, cnic_base=6100)
    (s1, _u1, _e1), (s2, _u2, _e2) = students
    sessions = await _create_n_sessions(app_client, offering_id, 3)
    # first class: both present
    assert (await app_client.post(f"/api/v1/attendance/sessions/{sessions[0]}/mark", json={"present_student_ids": [s1, s2], "late_note": LATE})).status_code == 200

    await _status(app_client, s2, "SUSPENDED")
    roster = {e["student_id"]: e for e in (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/roster")).json()}
    assert roster[s2]["student_status"] == "SUSPENDED" and roster[s1]["student_status"] == "ACTIVE"

    # nobody can mark them
    r = await app_client.post(f"/api/v1/attendance/sessions/{sessions[1]}/mark", json={"present_student_ids": [s1, s2], "late_note": LATE})
    assert r.status_code == 422 and "suspended" in r.text.lower()
    # the register is saved without them: no record, so the class does not count for or against them
    r = await app_client.post(f"/api/v1/attendance/sessions/{sessions[1]}/mark", json={"present_student_ids": [s1], "late_note": LATE})
    assert r.status_code == 200, r.text
    assert {x["student_id"] for x in r.json()} == {s1}
    pct = (await app_client.get(f"/api/v1/attendance/offerings/{offering_id}/students/{s2}/percentage")).json()
    assert pct["total_sessions_taken"] == 1 and pct["present_count"] == 1          # only the class before the suspension
    # the record from before the suspension is untouched
    old = (await db_session.execute(select(AttendanceRecord).where(AttendanceRecord.class_session_id == sessions[0]))).scalars().all()
    assert len(old) == 2

    # lifted: marked again like everyone else
    await _status(app_client, s2, "ACTIVE", "Suspension served")
    r = await app_client.post(f"/api/v1/attendance/sessions/{sessions[2]}/mark", json={"present_student_ids": [s1, s2], "late_note": LATE})
    assert r.status_code == 200 and {x["student_id"] for x in r.json()} == {s1, s2}


async def test_marks_are_locked_and_the_result_is_held_until_the_suspension_is_lifted(app_client, admin_user, db_session):
    offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="SUSB", n_students=2, cnic_base=6200)
    (s1, _u1, e1), (s2, _u2, e2) = students
    t = await _get_or_create_assessment_type(app_client, "Final SUS", 100)
    oa = (await app_client.post(f"/api/v1/academics/offerings/{offering_id}/assessments", json={"assessment_type_id": t})).json()["id"]
    assert (await app_client.post(f"/api/v1/academics/enrollments/{e1}/marks/{oa}", json={"obtained_marks": 80})).status_code in (200, 201)
    assert (await app_client.post(f"/api/v1/academics/enrollments/{e2}/marks/{oa}", json={"obtained_marks": 70})).status_code in (200, 201)

    await _status(app_client, s2, "SUSPENDED")
    r = await app_client.post(f"/api/v1/academics/enrollments/{e2}/marks/{oa}", json={"obtained_marks": 75})
    assert r.status_code == 422 and "suspended" in r.text.lower()

    # the class's result goes ahead without them; theirs is held
    await _publish_results(app_client, offering_id)
    db_session.expire_all()
    assert (await db_session.get(Enrollment, e1)).outcome is not None
    held = await db_session.get(Enrollment, e2)
    assert held.outcome is None
    # no result card while suspended (office copy)
    assert (await app_client.get(f"/api/v1/results/students/{s2}/transcript.pdf")).status_code == 422

    await _status(app_client, s2, "ACTIVE", "Suspension served")
    db_session.expire_all()
    assert (await db_session.get(Enrollment, e2)).outcome is not None              # worked out as soon as it was lifted
    assert (await app_client.get(f"/api/v1/results/students/{s2}/transcript.pdf")).status_code == 200


async def test_a_suspended_student_has_no_admit_card_and_no_certificate(app_client, admin_user):
    o1, students = await _setup_offering_with_enrolled_students(app_client, code="SUSC", n_students=1, cnic_base=6300)
    sid = students[0][0]
    off = await _term_of(app_client, o1)
    sched = await _schedule(app_client, off["term_label"])
    assert (await app_client.post(f"{E}/schedules/{sched}/papers", json=_paper(o1, off["term_start_date"][:8] + "22"))).status_code == 201
    assert (await app_client.post(f"{E}/schedules/{sched}/publish")).status_code == 200

    await _status(app_client, sid, "SUSPENDED")
    card = await app_client.get(f"{E}/schedules/{sched}/admit-cards/{sid}/pdf")
    assert card.status_code == 422 and "suspended" in card.text.lower()
    cert = await app_client.post(f"/api/v1/records/students/{sid}/certificates/bonafide")
    assert cert.status_code == 422 and "suspended" in cert.text.lower()

    await _status(app_client, sid, "ACTIVE", "Suspension served")
    assert (await app_client.get(f"{E}/schedules/{sched}/admit-cards/{sid}/pdf")).status_code == 200
    assert (await app_client.post(f"/api/v1/records/students/{sid}/certificates/bonafide")).status_code in (200, 201)


async def test_expelled_student_leaves_their_courses_and_gets_only_leaving_papers(app_client, admin_user, db_session):
    offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="SUSD", n_students=1, cnic_base=6400)
    sid, _u, eid = students[0]
    await _status(app_client, sid, "DROPPED", "Expelled by the disciplinary committee")
    db_session.expire_all()
    assert getattr((await db_session.get(Enrollment, eid)).status, "value", None) == "WITHDRAWN"
    roster = (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/roster")).json()
    assert all(e["status"] != "ENROLLED" for e in roster if e["student_id"] == sid)

    bona = await app_client.post(f"/api/v1/records/students/{sid}/certificates/bonafide")
    assert bona.status_code == 422
    mig = await app_client.post(f"/api/v1/records/students/{sid}/certificates/migration", data={"override_reason": "Clearance checked by hand"})
    assert mig.status_code in (200, 201), mig.text
