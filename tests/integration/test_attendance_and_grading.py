from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from app.core.clock import institute_now

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}

_DAY_NAMES = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
TERM_START = date(2026, 1, 5)
TERM_END = TERM_START + timedelta(weeks=9)  # up to 10 occurrences of the chosen weekday
SLOT_DAY_OF_WEEK = _DAY_NAMES[TERM_START.weekday()]


def _student_payload(**overrides):
    payload = {
        "first_name": "Att", "last_name": "Test",
        "date_of_birth": "2004-01-01", "gender": "Female",
        "cnic": "20001-0000001-1", "contact_no": "03002000000",
        "marital_status": "Single", "religion": "Islam", "nationality": "Pakistani",
        "guardian_name": "Guardian", "guardian_cnic": "20001-9999999-9",
        "relation_with_guardian": "Father", "emergency_contact_no": "03002009999",
        "present_address": "Somewhere", "province": "Punjab", "district": "Lahore", "city": "Lahore",
    }
    payload.update(overrides)
    return payload


async def _login(app_client, username, password):
    resp = await app_client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


async def _login_admin(app_client):
    await _login(app_client, ADMIN["username"], ADMIN["password"])


async def _create_and_activate_student(app_client, *, cnic, **overrides) -> tuple[str, str]:
    await _login_admin(app_client)
    create_resp = await app_client.post("/api/v1/students", json=_student_payload(cnic=cnic, **overrides))
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    student_id, username = body["id"], body["username"]
    await _login(app_client, username, "vertex123")
    change_resp = await app_client.post(
        "/api/v1/auth/change-password", json={"current_password": "vertex123", "new_password": "StudentPass123!"}
    )
    assert change_resp.status_code == 204
    await _login(app_client, username, "StudentPass123!")
    return student_id, username


async def _setup_offering_with_enrolled_students(app_client, *, code, n_students=1, cnic_base=2000):
    """Admin-created program+course+offering (open, linked at semester
    1, term dates spanning TERM_START..TERM_END so timetable slots can
    generate sessions), N students enrolled at semester 1. Returns
    (offering_id, [(student_id, username, enrollment_id), ...])."""
    await _login_admin(app_client)
    program_resp = await app_client.post(
        "/api/v1/programs", json={"code": code, "name": f"{code} Program", "duration_years": 4, "total_semesters": 8}
    )
    program_id = program_resp.json()["id"]
    course_resp = await app_client.post(
        "/api/v1/academics/courses", json={"code": f"{code}-C1", "title": "Test Course", "credit_hours": 3}
    )
    course_id = course_resp.json()["id"]
    await app_client.post(
        f"/api/v1/academics/courses/{course_id}/programs", json={"program_id": program_id, "semester_number": 1}
    )
    offering_resp = await app_client.post(
        "/api/v1/academics/offerings",
        json={
            "course_id": course_id, "term_label": "Fall 2026",
            "term_start_date": str(TERM_START), "term_end_date": str(TERM_END),
        },
    )
    offering_id = offering_resp.json()["id"]
    await app_client.patch(f"/api/v1/academics/offerings/{offering_id}", json={"is_open_for_enrollment": True})

    students = []
    for i in range(n_students):
        student_id, username = await _create_and_activate_student(
            app_client, cnic=f"{cnic_base}{i+2}-0000001-1", program_id=program_id, current_semester=1
        )
        await _login(app_client, username, "StudentPass123!")
        enroll_resp = await app_client.post(
            "/api/v1/academics/enrollments/me", json={"course_offering_id": offering_id}
        )
        assert enroll_resp.status_code == 201, enroll_resp.text
        students.append((student_id, username, enroll_resp.json()["id"]))

    await _login_admin(app_client)
    return offering_id, students


async def _get_or_create_period(app_client, start, end):
    existing = await app_client.get("/api/v1/academics/periods")
    for period in existing.json():
        if period["start_time"] == start and period["end_time"] == end:
            return period["id"]
    next_number = len(existing.json()) + 1
    resp = await app_client.post(
        "/api/v1/academics/periods",
        json={"period_number": next_number, "start_time": start, "end_time": end},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _create_n_sessions(app_client, offering_id, n, *, room="Room 1"):
    """Creates one recurring TimetableSlot on SLOT_DAY_OF_WEEK, which
    (given TERM_START/TERM_END cover 10 occurrences of that weekday)
    auto-generates real ClassSession rows. Returns the first n session
    ids, in chronological order."""
    period_id = await _get_or_create_period(app_client, "09:00:00", "10:00:00")
    slot_resp = await app_client.post(
        f"/api/v1/academics/offerings/{offering_id}/timetable-slots",
        json={"day_of_week": SLOT_DAY_OF_WEEK, "period_id": period_id, "room": room},
    )
    assert slot_resp.status_code == 201, slot_resp.text
    assert slot_resp.json()["sessions_generated"] >= n

    sessions_resp = await app_client.get(f"/api/v1/academics/offerings/{offering_id}/class-sessions")
    assert sessions_resp.status_code == 200, sessions_resp.text
    sessions = sorted(sessions_resp.json(), key=lambda s: s["session_date"])
    return [s["id"] for s in sessions[:n]]


async def _cancel_unmarked_sessions(app_client, offering_id):
    """Locking now requires every held class to be marked or cancelled. These
    tests only mark the classes they care about, so clear the rest first."""
    pending = (await app_client.get("/api/v1/attendance/needs-attention")).json()
    for item in pending:
        if item["course_offering_id"] == offering_id:
            r = await app_client.post(
                f"/api/v1/academics/class-sessions/{item['id']}/cancel", json={"reason": "test cleanup"}
            )
            assert r.status_code == 200, r.text


class TestMarkingAttendance:
    async def test_marking_a_session_records_present_and_absent_for_everyone_enrolled(
        self, app_client, admin_user, db_session
    ):
        offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="ATT1", n_students=2)
        session_id = (await _create_n_sessions(app_client, offering_id, 1))[0]
        present_id = students[0][0]

        mark_resp = await app_client.post(
            f"/api/v1/attendance/sessions/{session_id}/mark", json={"present_student_ids": [present_id], "late_note": "backfilled test data"}
        )
        assert mark_resp.status_code == 200, mark_resp.text
        records = {r["student_id"]: r["status"] for r in mark_resp.json()}
        assert records[students[0][0]] == "PRESENT"
        assert records[students[1][0]] == "ABSENT"

    async def test_marking_with_an_unenrolled_student_id_is_rejected(self, app_client, admin_user, db_session):
        offering_id, _students = await _setup_offering_with_enrolled_students(app_client, code="ATT2", n_students=1)
        session_id = (await _create_n_sessions(app_client, offering_id, 1))[0]
        fake_id = "00000000-0000-0000-0000-000000000099"
        resp = await app_client.post(
            f"/api/v1/attendance/sessions/{session_id}/mark", json={"present_student_ids": [fake_id], "late_note": "backfilled test data"}
        )
        assert resp.status_code == 422

    async def test_marking_again_corrects_in_place_not_duplicates(self, app_client, admin_user, db_session):
        offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="ATT3", n_students=1)
        session_id = (await _create_n_sessions(app_client, offering_id, 1))[0]
        student_id = students[0][0]

        await app_client.post(f"/api/v1/attendance/sessions/{session_id}/mark", json={"present_student_ids": [], "late_note": "backfilled test data"})
        second = await app_client.post(
            f"/api/v1/attendance/sessions/{session_id}/mark", json={"present_student_ids": [student_id], "late_note": "backfilled test data"}
        )
        assert len(second.json()) == 1
        assert second.json()[0]["status"] == "PRESENT"

    async def test_percentage_computation_correct(self, app_client, admin_user, db_session):
        offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="ATT4", n_students=1)
        student_id = students[0][0]
        session_ids = await _create_n_sessions(app_client, offering_id, 4)
        # Present for 3 of 4 sessions = 75%
        for i, session_id in enumerate(session_ids):
            present = [student_id] if i < 3 else []
            await app_client.post(f"/api/v1/attendance/sessions/{session_id}/mark", json={"present_student_ids": present, "late_note": "backfilled test data"})

        resp = await app_client.get(f"/api/v1/attendance/offerings/{offering_id}/students/{student_id}/percentage")
        assert resp.status_code == 200, resp.text
        assert resp.json()["percentage"] == 75.0
        assert resp.json()["total_sessions_taken"] == 4


class TestEarlyMarkingBlocked:
    async def test_teacher_and_admin_both_cannot_mark_a_session_before_it_starts(
        self, app_client, admin_user, db_session
    ):
        """Deliberately relative to the real current date (unlike the
        shared TERM_START/TERM_END constants above) — this test
        specifically needs a session that's genuinely in the future,
        including its start_time on today's date if chosen unluckily."""
        await _login_admin(app_client)
        future_start = institute_now() + timedelta(days=3)
        future_end = future_start + timedelta(weeks=9)
        course_resp = await app_client.post(
            "/api/v1/academics/courses", json={"code": "EARLYMARK-C", "title": "Test Course", "credit_hours": 3}
        )
        offering_resp = await app_client.post(
            "/api/v1/academics/offerings",
            json={
                "course_id": course_resp.json()["id"], "term_label": "Fall 2026",
                "term_start_date": str(future_start.date()), "term_end_date": str(future_end.date()),
            },
        )
        offering_id = offering_resp.json()["id"]
        future_day_of_week = _DAY_NAMES[future_start.weekday()]

        period_id = await _get_or_create_period(app_client, "09:00:00", "10:00:00")
        slot_resp = await app_client.post(
            f"/api/v1/academics/offerings/{offering_id}/timetable-slots",
            json={"day_of_week": future_day_of_week, "period_id": period_id, "room": "Room 30"},
        )
        assert slot_resp.status_code == 201, slot_resp.text
        sessions = (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/class-sessions")).json()
        session_id = sessions[0]["id"]

        # Admin gets no exemption here, unlike late marking.
        admin_attempt = await app_client.post(
            f"/api/v1/attendance/sessions/{session_id}/mark",
            json={"present_student_ids": [], "late_note": "trying anyway"},
        )
        assert admin_attempt.status_code == 422
        assert "hasn't started" in admin_attempt.json()["error"]["message"]


class TestLockAttendance:
    async def test_lock_stops_further_marking(self, app_client, admin_user, db_session):
        offering_id, _students = await _setup_offering_with_enrolled_students(app_client, code="LOCK1", n_students=1)
        session_id = (await _create_n_sessions(app_client, offering_id, 1))[0]

        await _cancel_unmarked_sessions(app_client, offering_id)
        lock_resp = await app_client.post(f"/api/v1/attendance/offerings/{offering_id}/lock")
        assert lock_resp.status_code == 200, lock_resp.text

        mark_resp = await app_client.post(f"/api/v1/attendance/sessions/{session_id}/mark", json={"present_student_ids": [], "late_note": "backfilled test data"})
        assert mark_resp.status_code == 422
        assert "locked" in mark_resp.json()["error"]["message"].lower()

    async def test_cannot_lock_twice(self, app_client, admin_user, db_session):
        offering_id, _ = await _setup_offering_with_enrolled_students(app_client, code="LOCK2", n_students=1)
        await app_client.post(f"/api/v1/attendance/offerings/{offering_id}/lock")
        second = await app_client.post(f"/api/v1/attendance/offerings/{offering_id}/lock")
        assert second.status_code == 422

    async def test_student_above_all_thresholds_is_unaffected(self, app_client, admin_user, db_session):
        offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="LOCK3", n_students=1)
        student_id, username, enrollment_id = students[0]
        session_ids = await _create_n_sessions(app_client, offering_id, 2)
        for session_id in session_ids:
            await app_client.post(f"/api/v1/attendance/sessions/{session_id}/mark", json={"present_student_ids": [student_id], "late_note": "backfilled test data"})

        await _cancel_unmarked_sessions(app_client, offering_id)
        lock_resp = await app_client.post(f"/api/v1/attendance/offerings/{offering_id}/lock")
        assert lock_resp.status_code == 200
        assert lock_resp.json()["dropped_count"] == 0
        assert lock_resp.json()["fined_count"] == 0

        enrollments = await app_client.get(f"/api/v1/academics/offerings/{offering_id}/roster")
        matching = next(e for e in enrollments.json() if e["id"] == enrollment_id)
        assert matching["status"] == "ENROLLED"

        # Login still works fine.
        await _login(app_client, username, "StudentPass123!")

    async def test_student_between_drop_and_fine_threshold_gets_fined_and_blocked(
        self, app_client, admin_user, db_session
    ):
        """Default thresholds: drop <=40%, fine <=65%. 5 sessions, 3
        present = 60% — between the two, so: fined, not dropped."""
        offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="LOCK4", n_students=1)
        student_id, username, enrollment_id = students[0]
        session_ids = await _create_n_sessions(app_client, offering_id, 5)
        for i, session_id in enumerate(session_ids):
            present = [student_id] if i < 3 else []
            await app_client.post(f"/api/v1/attendance/sessions/{session_id}/mark", json={"present_student_ids": present, "late_note": "backfilled test data"})

        await _cancel_unmarked_sessions(app_client, offering_id)
        lock_resp = await app_client.post(f"/api/v1/attendance/offerings/{offering_id}/lock")
        assert lock_resp.status_code == 200, lock_resp.text
        assert lock_resp.json()["fined_count"] == 1
        assert lock_resp.json()["dropped_count"] == 0

        # A real ATTENDANCE fine was issued.
        fines_resp = await app_client.get("/api/v1/fines")
        matching = [f for f in fines_resp.json() if f["payer_id"] == student_id and f["source"] == "ATTENDANCE"]
        assert len(matching) == 1, fines_resp.json()
        fine_id = matching[0]["id"]

        # Login is now blocked.
        login_resp = await app_client.post("/api/v1/auth/login", json={"username": username, "password": "StudentPass123!"})
        assert login_resp.status_code in (401, 403)

        # Enrollment itself is untouched (not dropped).
        enrollments = await app_client.get(f"/api/v1/academics/offerings/{offering_id}/roster")
        matching_enrollment = next(e for e in enrollments.json() if e["id"] == enrollment_id)
        assert matching_enrollment["status"] == "ENROLLED"

        # Reactivation refused while the fine is unpaid.
        reactivate_resp = await app_client.post(f"/api/v1/students/{student_id}/reactivate-after-attendance-fine")
        assert reactivate_resp.status_code == 422

        # Pay the fine, then reactivation succeeds and login works again.
        pay_resp = await app_client.post(f"/api/v1/fines/{fine_id}/mark-paid", json={"note": "Paid at accounts office"})
        assert pay_resp.status_code == 200, pay_resp.text
        reactivate_resp = await app_client.post(f"/api/v1/students/{student_id}/reactivate-after-attendance-fine")
        assert reactivate_resp.status_code == 204, reactivate_resp.text

        relogin_resp = await app_client.post("/api/v1/auth/login", json={"username": username, "password": "StudentPass123!"})
        assert relogin_resp.status_code == 200

    async def test_student_at_or_below_drop_threshold_is_auto_withdrawn_not_fined(
        self, app_client, admin_user, db_session
    ):
        """5 sessions, 1 present = 20% — at/below the 40% drop
        threshold, so: withdrawn, NOT fined (drop takes priority)."""
        offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="LOCK5", n_students=1)
        student_id, username, enrollment_id = students[0]
        session_ids = await _create_n_sessions(app_client, offering_id, 5)
        for i, session_id in enumerate(session_ids):
            present = [student_id] if i < 1 else []
            await app_client.post(f"/api/v1/attendance/sessions/{session_id}/mark", json={"present_student_ids": present, "late_note": "backfilled test data"})

        await _cancel_unmarked_sessions(app_client, offering_id)
        lock_resp = await app_client.post(f"/api/v1/attendance/offerings/{offering_id}/lock")
        assert lock_resp.status_code == 200, lock_resp.text
        assert lock_resp.json()["dropped_count"] == 1
        assert lock_resp.json()["fined_count"] == 0

        enrollments = await app_client.get(f"/api/v1/academics/offerings/{offering_id}/roster")
        matching = next(e for e in enrollments.json() if e["id"] == enrollment_id)
        assert matching["status"] == "WITHDRAWN"

        # No fine was issued, and login is unaffected (dropped =/= blocked).
        fines_resp = await app_client.get("/api/v1/fines")
        matching_fines = [f for f in fines_resp.json() if f["payer_id"] == student_id and f["source"] == "ATTENDANCE"]
        assert len(matching_fines) == 0
        await _login(app_client, username, "StudentPass123!")


async def _get_or_create_assessment_type(app_client, name, max_marks):
    """Each test gets an isolated DB, so names never collide across
    tests -- but a single test may need several distinct types."""
    existing = await app_client.get("/api/v1/academics/assessment-types")
    for t in existing.json():
        if t["name"] == name:
            return t["id"]
    resp = await app_client.post(
        "/api/v1/academics/assessment-types", json={"name": name, "max_marks": max_marks}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _publish_results(client, offering_id):
    """The marks are a draft until submitted, approved and published; only then is pass/fail decided."""
    for step in (f"/api/v1/results/offerings/{offering_id}/submit", f"/api/v1/results/sheets/{offering_id}/approve", f"/api/v1/results/sheets/{offering_id}/publish"):
        r = await client.post(step)
        assert r.status_code == 200, (step, r.text)


class TestGrading:
    async def test_cannot_add_the_same_catalog_type_to_an_offering_twice(self, app_client, admin_user, db_session):
        offering_id, _ = await _setup_offering_with_enrolled_students(app_client, code="GRADE1", n_students=1)
        type_id = await _get_or_create_assessment_type(app_client, "Midterm", 50)
        first = await app_client.post(
            f"/api/v1/academics/offerings/{offering_id}/assessments", json={"assessment_type_id": type_id}
        )
        assert first.status_code == 201, first.text
        second = await app_client.post(
            f"/api/v1/academics/offerings/{offering_id}/assessments", json={"assessment_type_id": type_id}
        )
        assert second.status_code == 422

    async def test_outcome_only_computes_once_every_assessment_has_a_mark(self, app_client, admin_user, db_session):
        offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="GRADE2", n_students=1)
        _student_id, _username, enrollment_id = students[0]
        quiz_type = await _get_or_create_assessment_type(app_client, "Quiz", 20)
        final_type = await _get_or_create_assessment_type(app_client, "Final", 100)
        c1 = (await app_client.post(
            f"/api/v1/academics/offerings/{offering_id}/assessments", json={"assessment_type_id": quiz_type}
        )).json()["id"]
        c2 = (await app_client.post(
            f"/api/v1/academics/offerings/{offering_id}/assessments", json={"assessment_type_id": final_type}
        )).json()["id"]

        await app_client.post(f"/api/v1/academics/enrollments/{enrollment_id}/marks/{c1}", json={"obtained_marks": 18})
        enrollments = await app_client.get(f"/api/v1/academics/offerings/{offering_id}/roster")
        matching = next(e for e in enrollments.json() if e["id"] == enrollment_id)
        assert matching["status"] == "ENROLLED"  # still incomplete, no outcome yet

        await app_client.post(f"/api/v1/academics/enrollments/{enrollment_id}/marks/{c2}", json={"obtained_marks": 70})
        enrollments = await app_client.get(f"/api/v1/academics/offerings/{offering_id}/roster")
        matching = next(e for e in enrollments.json() if e["id"] == enrollment_id)
        assert matching["status"] == "ENROLLED" and matching["outcome"] is None  # every mark is in, but nothing is decided until the results are published

        await _publish_results(app_client, offering_id)
        enrollments = await app_client.get(f"/api/v1/academics/offerings/{offering_id}/roster")
        matching = next(e for e in enrollments.json() if e["id"] == enrollment_id)
        assert matching["status"] == "COMPLETED"
        # Raw sum: (18+70)/(20+100) = 88/120 = 73.33% -> PASSED
        assert matching["outcome"] == "PASSED"

    async def test_low_attendance_forces_failed_even_with_a_high_score(self, app_client, admin_user, db_session):
        offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="GRADE3", n_students=1)
        _student_id, _username, enrollment_id = students[0]
        # 2 sessions, 0 present = 0% attendance, well below the 75% eligibility rule.
        session_ids = await _create_n_sessions(app_client, offering_id, 2)
        for session_id in session_ids:
            await app_client.post(f"/api/v1/attendance/sessions/{session_id}/mark", json={"present_student_ids": [], "late_note": "backfilled test data"})

        only_type = await _get_or_create_assessment_type(app_client, "Only Assessment", 100)
        c1 = (await app_client.post(
            f"/api/v1/academics/offerings/{offering_id}/assessments", json={"assessment_type_id": only_type}
        )).json()["id"]
        await app_client.post(f"/api/v1/academics/enrollments/{enrollment_id}/marks/{c1}", json={"obtained_marks": 95})

        await _publish_results(app_client, offering_id)
        enrollments = await app_client.get(f"/api/v1/academics/offerings/{offering_id}/roster")
        matching = next(e for e in enrollments.json() if e["id"] == enrollment_id)
        assert matching["status"] == "COMPLETED"
        assert matching["outcome"] == "FAILED"  # 95% score, but attendance-ineligible

    async def test_cannot_remove_an_assessment_once_a_mark_is_recorded(self, app_client, admin_user, db_session):
        offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="GRADE4", n_students=1)
        _student_id, _username, enrollment_id = students[0]
        assessment_type = await _get_or_create_assessment_type(app_client, "Removable Test", 10)
        oa = (await app_client.post(
            f"/api/v1/academics/offerings/{offering_id}/assessments", json={"assessment_type_id": assessment_type}
        )).json()

        # Removable before any mark exists.
        remove_resp = await app_client.delete(f"/api/v1/academics/offering-assessments/{oa['id']}")
        assert remove_resp.status_code == 204

        # Re-add, mark it, then confirm it can no longer be removed.
        oa2 = (await app_client.post(
            f"/api/v1/academics/offerings/{offering_id}/assessments", json={"assessment_type_id": assessment_type}
        )).json()
        await app_client.post(f"/api/v1/academics/enrollments/{enrollment_id}/marks/{oa2['id']}", json={"obtained_marks": 5})
        blocked_resp = await app_client.delete(f"/api/v1/academics/offering-assessments/{oa2['id']}")
        assert blocked_resp.status_code == 422