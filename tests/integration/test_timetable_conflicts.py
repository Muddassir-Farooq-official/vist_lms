from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from app.core.clock import institute_today

from app.employees.models import JobRole

pytestmark = pytest.mark.asyncio

ADMIN = {"username": "admin_test", "password": "AdminPass123!"}

_DAY_NAMES = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
TERM_START = date(2026, 1, 5)
TERM_END = TERM_START + timedelta(weeks=9)
SLOT_DAY_OF_WEEK = _DAY_NAMES[TERM_START.weekday()]
OTHER_DAY_OF_WEEK = _DAY_NAMES[(TERM_START.weekday() + 1) % 7]


def _employee_payload(job_role_id, **overrides):
    payload = {
        "first_name": "Conf", "last_name": "Lict",
        "date_of_birth": "1985-01-01", "gender": "Male",
        "cnic": "70001-0000001-1", "contact_no": "03007770000",
        "hire_date": "2026-08-01", "department": "Nursing",
        "job_role_id": str(job_role_id), "base_salary": 80000,
        "password": "TeacherPass123!",
    }
    payload.update(overrides)
    return payload


async def _login(app_client, username, password):
    resp = await app_client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


async def _login_admin(app_client):
    await _login(app_client, ADMIN["username"], ADMIN["password"])


async def _create_teacher(app_client, db_session, *, code, cnic) -> str:
    job_role = JobRole(code=code, title="Teacher", default_role_code="TEACHER")
    db_session.add(job_role)
    await db_session.commit()
    await _login_admin(app_client)
    resp = await app_client.post("/api/v1/employees", json=_employee_payload(job_role.id, cnic=cnic))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _create_offering(app_client, *, code, teacher_id=None, program_id=None, semester_number=1) -> tuple[str, str]:
    """Returns (course_id, offering_id). If program_id is given, links
    the course into that program's curriculum at semester_number --
    what the student-batch conflict check actually keys off."""
    course_resp = await app_client.post(
        "/api/v1/academics/courses", json={"code": f"{code}-C", "title": "Test Course", "credit_hours": 3}
    )
    assert course_resp.status_code == 201, course_resp.text
    course_id = course_resp.json()["id"]

    if program_id is not None:
        link_resp = await app_client.post(
            f"/api/v1/academics/courses/{course_id}/programs",
            json={"program_id": program_id, "semester_number": semester_number},
        )
        assert link_resp.status_code == 201, link_resp.text

    offering_resp = await app_client.post(
        "/api/v1/academics/offerings",
        json={
            "course_id": course_id, "term_label": "Fall 2026", "assigned_teacher_id": teacher_id,
            "term_start_date": str(TERM_START), "term_end_date": str(TERM_END),
        },
    )
    assert offering_resp.status_code == 201, offering_resp.text
    return course_id, offering_resp.json()["id"]


async def _create_program(app_client, *, code) -> str:
    resp = await app_client.post(
        "/api/v1/programs", json={"code": code, "name": f"{code} Program", "duration_years": 4, "total_semesters": 8}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _get_or_create_period(app_client, start, end):
    """Each test gets an isolated DB, so periods never collide across
    tests -- but a single test may ask for several distinct time
    ranges (to set up overlap scenarios), so reuse an existing period
    with a matching start/end rather than creating a duplicate."""
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


async def _create_slot(app_client, offering_id, *, day_of_week=SLOT_DAY_OF_WEEK, start="09:00:00", end="10:00:00", room="Room 1"):
    period_id = await _get_or_create_period(app_client, start, end)
    return await app_client.post(
        f"/api/v1/academics/offerings/{offering_id}/timetable-slots",
        json={"day_of_week": day_of_week, "period_id": period_id, "room": room},
    )


class TestSlotCreationPrerequisites:
    async def test_cannot_create_slot_without_term_dates(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        course_resp = await app_client.post(
            "/api/v1/academics/courses", json={"code": "NODATE-C", "title": "No Dates", "credit_hours": 3}
        )
        offering_resp = await app_client.post(
            "/api/v1/academics/offerings", json={"course_id": course_resp.json()["id"], "term_label": "Fall 2026"}
        )
        offering_id = offering_resp.json()["id"]

        resp = await _create_slot(app_client, offering_id)
        assert resp.status_code == 422
        assert "term" in resp.json()["error"]["message"].lower()

    async def test_period_start_time_must_be_before_end_time(self, app_client, admin_user, db_session):
        """The start<end check now lives on Period creation, not slot
        creation -- a slot's time always comes from a valid period."""
        await _login_admin(app_client)
        resp = await app_client.post(
            "/api/v1/academics/periods",
            json={"period_number": 1, "start_time": "11:00:00", "end_time": "10:00:00"},
        )
        assert resp.status_code == 422

    async def test_creating_a_slot_generates_the_right_number_of_sessions(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        _course_id, offering_id = await _create_offering(app_client, code="GENCOUNT")
        resp = await _create_slot(app_client, offering_id)
        assert resp.status_code == 201, resp.text
        # TERM_START..TERM_END spans exactly 10 occurrences of SLOT_DAY_OF_WEEK (9 weeks apart + the start itself).
        assert resp.json()["sessions_generated"] == 10


class TestRoomConflict:
    async def test_same_room_same_day_overlapping_time_is_rejected(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        _c1, offering_a = await _create_offering(app_client, code="ROOM1A")
        _c2, offering_b = await _create_offering(app_client, code="ROOM1B")

        first = await _create_slot(app_client, offering_a, room="Room 5", start="09:00:00", end="10:00:00")
        assert first.status_code == 201, first.text

        second = await _create_slot(app_client, offering_b, room="Room 5", start="09:00:00", end="10:00:00")
        assert second.status_code == 422
        assert "room" in second.json()["error"]["message"].lower()

    async def test_same_room_same_day_non_overlapping_time_is_allowed(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        _c1, offering_a = await _create_offering(app_client, code="ROOM2A")
        _c2, offering_b = await _create_offering(app_client, code="ROOM2B")

        first = await _create_slot(app_client, offering_a, room="Room 6", start="09:00:00", end="10:00:00")
        assert first.status_code == 201, first.text

        second = await _create_slot(app_client, offering_b, room="Room 6", start="10:00:00", end="11:00:00")
        assert second.status_code == 201, second.text

    async def test_same_room_different_day_overlapping_time_is_allowed(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        _c1, offering_a = await _create_offering(app_client, code="ROOM3A")
        _c2, offering_b = await _create_offering(app_client, code="ROOM3B")

        first = await _create_slot(app_client, offering_a, room="Room 7", day_of_week=SLOT_DAY_OF_WEEK)
        assert first.status_code == 201, first.text

        second = await _create_slot(app_client, offering_b, room="Room 7", day_of_week=OTHER_DAY_OF_WEEK)
        assert second.status_code == 201, second.text

    async def test_different_room_same_time_is_allowed(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        _c1, offering_a = await _create_offering(app_client, code="ROOM4A")
        _c2, offering_b = await _create_offering(app_client, code="ROOM4B")

        first = await _create_slot(app_client, offering_a, room="Room 8")
        assert first.status_code == 201, first.text

        second = await _create_slot(app_client, offering_b, room="Room 9")
        assert second.status_code == 201, second.text


class TestTeacherConflict:
    async def test_same_teacher_overlapping_time_across_different_offerings_is_rejected(
        self, app_client, admin_user, db_session
    ):
        teacher_id = await _create_teacher(app_client, db_session, code="TCONF1", cnic="71001-0000001-1")
        await _login_admin(app_client)
        _c1, offering_a = await _create_offering(app_client, code="TEACH1A", teacher_id=teacher_id)
        _c2, offering_b = await _create_offering(app_client, code="TEACH1B", teacher_id=teacher_id)

        first = await _create_slot(app_client, offering_a, room="Room 10", start="09:00:00", end="10:00:00")
        assert first.status_code == 201, first.text

        second = await _create_slot(app_client, offering_b, room="Room 11", start="09:00:00", end="10:00:00")
        assert second.status_code == 422
        assert "teacher" in second.json()["error"]["message"].lower()

    async def test_different_teachers_overlapping_time_different_rooms_is_allowed(
        self, app_client, admin_user, db_session
    ):
        teacher_a = await _create_teacher(app_client, db_session, code="TCONF2A", cnic="71002-0000001-1")
        teacher_b = await _create_teacher(app_client, db_session, code="TCONF2B", cnic="71003-0000001-1")
        await _login_admin(app_client)
        _c1, offering_a = await _create_offering(app_client, code="TEACH2A", teacher_id=teacher_a)
        _c2, offering_b = await _create_offering(app_client, code="TEACH2B", teacher_id=teacher_b)

        first = await _create_slot(app_client, offering_a, room="Room 12")
        assert first.status_code == 201, first.text

        second = await _create_slot(app_client, offering_b, room="Room 13")
        assert second.status_code == 201, second.text

    async def test_unassigned_offering_has_no_teacher_conflict_check(self, app_client, admin_user, db_session):
        """No assigned_teacher_id -> nothing to conflict-check against."""
        await _login_admin(app_client)
        _c1, offering_a = await _create_offering(app_client, code="TEACH3A")
        _c2, offering_b = await _create_offering(app_client, code="TEACH3B")

        first = await _create_slot(app_client, offering_a, room="Room 14")
        assert first.status_code == 201, first.text
        second = await _create_slot(app_client, offering_b, room="Room 15")
        assert second.status_code == 201, second.text


class TestStudentBatchConflict:
    async def test_same_program_and_semester_overlapping_time_across_different_courses_is_rejected(
        self, app_client, admin_user, db_session
    ):
        await _login_admin(app_client)
        program_id = await _create_program(app_client, code="BATCH1")
        _c1, offering_a = await _create_offering(app_client, code="BATCH1A", program_id=program_id, semester_number=1)
        _c2, offering_b = await _create_offering(app_client, code="BATCH1B", program_id=program_id, semester_number=1)

        first = await _create_slot(app_client, offering_a, room="Room 16", start="09:00:00", end="10:00:00")
        assert first.status_code == 201, first.text

        second = await _create_slot(app_client, offering_b, room="Room 17", start="09:00:00", end="10:00:00")
        assert second.status_code == 422
        assert "semester" in second.json()["error"]["message"].lower() or "student" in second.json()["error"]["message"].lower()

    async def test_same_program_different_semester_overlapping_time_is_allowed(self, app_client, admin_user, db_session):
        """Semester 1 and semester 3 students aren't the same batch --
        no conflict even though it's the same program."""
        await _login_admin(app_client)
        program_id = await _create_program(app_client, code="BATCH2")
        _c1, offering_a = await _create_offering(app_client, code="BATCH2A", program_id=program_id, semester_number=1)
        _c2, offering_b = await _create_offering(app_client, code="BATCH2B", program_id=program_id, semester_number=3)

        first = await _create_slot(app_client, offering_a, room="Room 18")
        assert first.status_code == 201, first.text
        second = await _create_slot(app_client, offering_b, room="Room 19")
        assert second.status_code == 201, second.text

    async def test_different_programs_same_semester_overlapping_time_is_allowed(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        program_a = await _create_program(app_client, code="BATCH3A")
        program_b = await _create_program(app_client, code="BATCH3B")
        _c1, offering_a = await _create_offering(app_client, code="BATCH3OFA", program_id=program_a, semester_number=1)
        _c2, offering_b = await _create_offering(app_client, code="BATCH3OFB", program_id=program_b, semester_number=1)

        first = await _create_slot(app_client, offering_a, room="Room 20")
        assert first.status_code == 201, first.text
        second = await _create_slot(app_client, offering_b, room="Room 21")
        assert second.status_code == 201, second.text

    async def test_course_with_no_program_link_has_no_batch_conflict_check(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        _c1, offering_a = await _create_offering(app_client, code="BATCH4A")  # no program link
        _c2, offering_b = await _create_offering(app_client, code="BATCH4B")  # no program link

        first = await _create_slot(app_client, offering_a, room="Room 22")
        assert first.status_code == 201, first.text
        second = await _create_slot(app_client, offering_b, room="Room 23")
        assert second.status_code == 201, second.text


class TestDeleteSlot:
    async def test_deleting_a_slot_cancels_future_sessions_but_not_past_ones(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        # Deliberately relative to the real current date (unlike the
        # shared TERM_START/TERM_END constants above, which are just
        # arbitrary fixed dates for the other tests that don't care
        # about past-vs-future) -- this test specifically needs every
        # generated session to be genuinely in the future.
        future_start = institute_today() + timedelta(days=7)
        future_end = future_start + timedelta(weeks=9)
        course_resp = await app_client.post(
            "/api/v1/academics/courses", json={"code": "DELSLOT1-C", "title": "Test Course", "credit_hours": 3}
        )
        offering_resp = await app_client.post(
            "/api/v1/academics/offerings",
            json={
                "course_id": course_resp.json()["id"], "term_label": "Fall 2026",
                "term_start_date": str(future_start), "term_end_date": str(future_end),
            },
        )
        offering_id = offering_resp.json()["id"]
        future_day_of_week = _DAY_NAMES[future_start.weekday()]

        period_id = await _get_or_create_period(app_client, "09:00:00", "10:00:00")
        slot_resp = await app_client.post(
            f"/api/v1/academics/offerings/{offering_id}/timetable-slots",
            json={"day_of_week": future_day_of_week, "period_id": period_id, "room": "Room 24"},
        )
        assert slot_resp.status_code == 201, slot_resp.text
        slot_id = slot_resp.json()["slot"]["id"]

        sessions_before = (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/class-sessions")).json()
        assert all(s["status"] == "SCHEDULED" for s in sessions_before)
        assert len(sessions_before) == 10

        delete_resp = await app_client.delete(f"/api/v1/academics/timetable-slots/{slot_id}")
        assert delete_resp.status_code == 200, delete_resp.text
        assert delete_resp.json()["future_sessions_cancelled"] == len(sessions_before)

        sessions_after = (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/class-sessions")).json()
        assert all(s["status"] == "CANCELLED" for s in sessions_after)
        assert all(s["timetable_slot_id"] is None for s in sessions_after)

    async def test_deleting_a_slot_frees_up_its_room_and_time_for_a_new_slot(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        _c1, offering_a = await _create_offering(app_client, code="DELSLOT2A")
        _c2, offering_b = await _create_offering(app_client, code="DELSLOT2B")

        first = await _create_slot(app_client, offering_a, room="Room 25", start="09:00:00", end="10:00:00")
        slot_id = first.json()["slot"]["id"]

        blocked = await _create_slot(app_client, offering_b, room="Room 25", start="09:00:00", end="10:00:00")
        assert blocked.status_code == 422

        await app_client.delete(f"/api/v1/academics/timetable-slots/{slot_id}")

        now_allowed = await _create_slot(app_client, offering_b, room="Room 25", start="09:00:00", end="10:00:00")
        assert now_allowed.status_code == 201, now_allowed.text


class TestPerSessionOverride:
    async def test_editing_one_session_does_not_affect_others(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        _c1, offering_id = await _create_offering(app_client, code="OVERRIDE1")
        await _create_slot(app_client, offering_id, room="Room 26")
        sessions = (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/class-sessions")).json()
        sessions.sort(key=lambda s: s["session_date"])
        first_id, second_id = sessions[0]["id"], sessions[1]["id"]

        patch_resp = await app_client.patch(
            f"/api/v1/academics/class-sessions/{first_id}",
            json={"room": "Room 99", "title": "Guest lecture today"},
        )
        assert patch_resp.status_code == 200, patch_resp.text
        assert patch_resp.json()["room"] == "Room 99"
        assert patch_resp.json()["title"] == "Guest lecture today"

        unaffected = (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/class-sessions")).json()
        second_after = next(s for s in unaffected if s["id"] == second_id)
        assert second_after["room"] == "Room 26"
        assert second_after["title"] is None

    async def test_cancelling_one_session_does_not_affect_others_or_require_a_reason_on_them(
        self, app_client, admin_user, db_session
    ):
        await _login_admin(app_client)
        _c1, offering_id = await _create_offering(app_client, code="OVERRIDE2")
        await _create_slot(app_client, offering_id, room="Room 27")
        sessions = (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/class-sessions")).json()
        sessions.sort(key=lambda s: s["session_date"])
        holiday_id = sessions[0]["id"]

        cancel_resp = await app_client.post(
            f"/api/v1/academics/class-sessions/{holiday_id}/cancel", json={"reason": "Public holiday"}
        )
        assert cancel_resp.status_code == 200, cancel_resp.text
        assert cancel_resp.json()["status"] == "CANCELLED"
        assert cancel_resp.json()["cancelled_reason"] == "Public holiday"

        others = (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/class-sessions")).json()
        assert sum(1 for s in others if s["status"] == "CANCELLED") == 1
        assert sum(1 for s in others if s["status"] == "SCHEDULED") == len(others) - 1

    async def test_cannot_mark_attendance_for_a_cancelled_session(self, app_client, admin_user, db_session):
        await _login_admin(app_client)
        _c1, offering_id = await _create_offering(app_client, code="OVERRIDE3")
        await _create_slot(app_client, offering_id, room="Room 28")
        sessions = (await app_client.get(f"/api/v1/academics/offerings/{offering_id}/class-sessions")).json()
        session_id = sessions[0]["id"]
        await app_client.post(f"/api/v1/academics/class-sessions/{session_id}/cancel", json={"reason": "Holiday"})

        mark_resp = await app_client.post(
            f"/api/v1/attendance/sessions/{session_id}/mark", json={"present_student_ids": [], "late_note": "test"}
        )
        assert mark_resp.status_code == 422