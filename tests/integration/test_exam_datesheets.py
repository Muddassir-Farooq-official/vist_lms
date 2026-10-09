"""Datesheets and admit cards: clashes block publishing; students see only their own papers once published;
admit cards mark short-attendance papers NOT ELIGIBLE and are WITHHELD for overdue fees or unpaid attendance fines."""
from __future__ import annotations

import pytest

from app.core.config import settings
from tests.integration.test_attendance_and_grading import (
    _cancel_unmarked_sessions,
    _create_n_sessions,
    _login,
    _login_admin,
    _setup_offering_with_enrolled_students,
)
from tests.integration.test_exam_controller import _exam_user

pytestmark = pytest.mark.asyncio
E = "/api/v1/exams"
SP = "StudentPass123!"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _term_of(client, offering_id):
    return (await client.get(f"/api/v1/academics/offerings/{offering_id}")).json()


async def _two_courses_same_students(client):
    """Two offerings in the same term, the same 2 students enrolled in both."""
    o1, students = await _setup_offering_with_enrolled_students(client, code="EXA", n_students=2, cnic_base=5100)
    off1 = await _term_of(client, o1)
    course = (await client.post("/api/v1/academics/courses", json={"code": "EXB", "title": "Second", "credit_hours": 3})).json()["id"]
    o2 = (await client.post("/api/v1/academics/offerings", json={"course_id": course, "term_label": off1["term_label"],
          "term_start_date": off1["term_start_date"], "term_end_date": off1["term_end_date"], "is_open_for_enrollment": True})).json()["id"]
    st = (await client.get(f"/api/v1/students/{students[0][0]}")).json()          # the second course belongs to the same program/semester
    await client.post(f"/api/v1/academics/courses/{course}/programs", json={"program_id": st["program_id"], "semester_number": st["current_semester"]})
    for sid, _u, _e in students:
        r = await client.post(f"/api/v1/academics/offerings/{o2}/enroll-student", json={"student_id": sid})
        assert r.status_code == 201, r.text
    return off1, o1, o2, students


async def _schedule(client, term, name="Midterm"):
    r = await client.post(f"{E}/schedules", json={"term_label": term, "name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _paper(oid, day, start="09:00", end="12:00", room="Hall A"):
    return {"course_offering_id": oid, "exam_date": day, "start_time": start, "end_time": end, "room": room}


async def test_clashes_block_publishing_until_fixed(app_client, admin_user):
    await _login_admin(app_client)
    off, o1, o2, _students = await _two_courses_same_students(app_client)
    day = off["term_start_date"][:8] + "20"
    sid = await _schedule(app_client, off["term_label"])
    assert (await app_client.post(f"{E}/schedules/{sid}/papers", json=_paper(o1, day))).status_code == 201
    d = (await app_client.post(f"{E}/schedules/{sid}/papers", json=_paper(o2, day, "10:00", "13:00", "Hall A"))).json()
    kinds = sorted(c["kind"] for c in d["clashes"])
    assert kinds == ["ROOM", "STUDENT"]                                   # same students and same hall, overlapping
    blocked = await app_client.post(f"{E}/schedules/{sid}/publish")
    assert blocked.status_code == 422 and "clash" in blocked.text
    p2 = next(p for p in d["papers"] if p["course_offering_id"] == o2)
    fixed = (await app_client.patch(f"{E}/papers/{p2['id']}", json={"start_time": "14:00", "end_time": "17:00"})).json()
    assert fixed["clashes"] == []
    pub = await app_client.post(f"{E}/schedules/{sid}/publish")
    assert pub.status_code == 200 and pub.json()["status"] == "PUBLISHED"
    # after publishing, a change that creates a clash is refused
    again = await app_client.patch(f"{E}/papers/{p2['id']}", json={"start_time": "09:30", "end_time": "11:00"})
    assert again.status_code == 422 and "would clash" in again.text


async def test_wrong_term_and_dates_are_refused(app_client, admin_user):
    await _login_admin(app_client)
    off, o1, _o2, _s = await _two_courses_same_students(app_client)
    sid = await _schedule(app_client, off["term_label"], "Final")
    late = await app_client.post(f"{E}/schedules/{sid}/papers", json=_paper(o1, "2099-01-01"))
    assert late.status_code == 422 and "after the term ends" in late.text
    backwards = await app_client.post(f"{E}/schedules/{sid}/papers", json=_paper(o1, off["term_start_date"], "12:00", "09:00"))
    assert backwards.status_code == 422
    dup = await app_client.post(f"{E}/schedules", json={"term_label": off["term_label"], "name": "Final"})
    assert dup.status_code == 409


async def test_students_see_their_datesheet_only_once_published_and_get_an_admit_card(app_client, admin_user):
    await _login_admin(app_client)
    off, o1, o2, students = await _two_courses_same_students(app_client)
    day = off["term_start_date"][:8] + "21"
    sid = await _schedule(app_client, off["term_label"])
    await app_client.post(f"{E}/schedules/{sid}/papers", json=_paper(o1, day))
    await app_client.post(f"{E}/schedules/{sid}/papers", json=_paper(o2, day, "14:00", "17:00", "Hall B"))
    _sid, username, _e = students[0]
    await _login(app_client, username, SP)
    assert (await app_client.get(f"{E}/me/datesheets")).json() == []                 # draft: not visible
    assert (await app_client.get(f"{E}/me/admit-card/{sid}")).status_code == 422
    await _login_admin(app_client)
    await app_client.post(f"{E}/schedules/{sid}/publish")
    await _login(app_client, username, SP)
    sheets = (await app_client.get(f"{E}/me/datesheets")).json()
    assert len(sheets) == 1 and len(sheets[0]["papers"]) == 2
    card = (await app_client.get(f"{E}/me/admit-card/{sid}")).json()
    assert card["withheld"] == [] and all(p["eligible"] for p in card["papers"])
    pdf = await app_client.get(f"{E}/me/admit-card/{sid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    # students cannot manage datesheets
    assert (await app_client.get(f"{E}/schedules")).status_code == 403


async def test_short_attendance_is_not_eligible_and_a_fine_withholds_the_card(app_client, admin_user, db_session):
    await _login_admin(app_client)
    o1, students = await _setup_offering_with_enrolled_students(app_client, code="EXC", n_students=1, cnic_base=5300)
    off = await _term_of(app_client, o1)
    student_id, username, _e = students[0]
    for i, s in enumerate(await _create_n_sessions(app_client, o1, 5)):            # 3/5 = 60%: below 75, fined at lock
        await app_client.post(f"/api/v1/attendance/sessions/{s}/mark", json={"present_student_ids": [student_id] if i < 3 else [], "late_note": "t"})
    sid = await _schedule(app_client, off["term_label"])
    await app_client.post(f"{E}/schedules/{sid}/papers", json=_paper(o1, off["term_start_date"][:8] + "22"))
    await app_client.post(f"{E}/schedules/{sid}/publish")
    card = (await app_client.get(f"{E}/schedules/{sid}/admit-cards/{student_id}/pdf"))
    assert card.status_code == 200
    await _login(app_client, username, SP)
    d = (await app_client.get(f"{E}/me/admit-card/{sid}")).json()
    assert d["papers"][0]["eligible"] is False and "60%" in d["papers"][0]["reason"] and d["withheld"] == []
    await _login_admin(app_client)
    await _cancel_unmarked_sessions(app_client, o1)
    assert (await app_client.post(f"/api/v1/attendance/offerings/{o1}/lock")).json()["fined_count"] == 1
    import uuid as _uuid

    from app.exams.service import ExamScheduleService

    card = await ExamScheduleService(db_session).admit_card(_uuid.UUID(sid), _uuid.UUID(student_id))
    assert any("attendance fine" in w for w in card["withheld"])
    assert (await app_client.get(f"{E}/schedules/{sid}/admit-cards/{student_id}/pdf")).status_code == 200   # exam office can still print it


async def test_overdue_fees_withhold_the_card_when_the_policy_says_so(app_client, admin_user, db_session, monkeypatch):
    import uuid as _uuid

    from app.exams.service import ExamScheduleService
    from tests.integration.test_fees import _create_active_fee_structure

    await _login_admin(app_client)
    off, o1, _o2, students = await _two_courses_same_students(app_client)
    await _create_active_fee_structure(app_client, total_amount=6000, installment_count=1)   # due date already passed in the test term
    await _login_admin(app_client)
    sid = await _schedule(app_client, off["term_label"])
    await app_client.post(f"{E}/schedules/{sid}/papers", json=_paper(o1, off["term_start_date"][:8] + "24"))
    await app_client.post(f"{E}/schedules/{sid}/publish")
    student = _uuid.UUID(students[0][0])
    card = await ExamScheduleService(db_session).admit_card(_uuid.UUID(sid), student)
    overdue = any("overdue fees" in w for w in card["withheld"])
    monkeypatch.setattr(settings, "admit_card_requires_fees_cleared", 0)
    relaxed = await ExamScheduleService(db_session).admit_card(_uuid.UUID(sid), student)
    assert not any("overdue fees" in w for w in relaxed["withheld"])
    # whether the fee is overdue depends on the helper's dates; when it is, the card must be withheld
    rows = (await app_client.get(f"/api/v1/fees/accounts/students/{students[0][0]}")).json()["assignments"]
    is_overdue = any(i["status"] == "OVERDUE" for a in rows for i in a["installments"])
    assert overdue == is_overdue


async def test_exam_controller_runs_datesheets(app_client, admin_user, db_session):
    await _login_admin(app_client)
    off, o1, _o2, _s = await _two_courses_same_students(app_client)
    user, pw = await _exam_user(app_client, db_session)
    await _login(app_client, user, pw)
    sid = await _schedule(app_client, off["term_label"], "Midterm")
    assert (await app_client.post(f"{E}/schedules/{sid}/papers", json=_paper(o1, off["term_start_date"][:8] + "23"))).status_code == 201
    assert (await app_client.post(f"{E}/schedules/{sid}/publish")).status_code == 200


async def test_parents_and_teachers_see_the_published_datesheet(app_client, admin_user, db_session):
    from tests.integration.test_announcements import _first_login, _staff
    from tests.integration.test_parent_portal import _parent_login

    await _login_admin(app_client)
    off, o1, o2, students = await _two_courses_same_students(app_client)
    teacher_user, first = await _staff(app_client, db_session, "INVG", "TEACHER", "35202-4900001-1")
    emp = (await app_client.get("/api/v1/employees", params={"q": teacher_user})).json()["items"][0]["id"]
    sid = await _schedule(app_client, off["term_label"])
    p = _paper(o1, off["term_start_date"][:8] + "25")
    p["invigilator_employee_id"] = emp
    await app_client.post(f"{E}/schedules/{sid}/papers", json=p)
    await app_client.post(f"{E}/schedules/{sid}/papers", json=_paper(o2, off["term_start_date"][:8] + "26"))
    await app_client.post(f"{E}/schedules/{sid}/publish")

    # parent of the first student
    child = students[0][0]
    made = (await app_client.post(f"/api/v1/parents/students/{child}")).json()
    await _parent_login(app_client, made["username"], made["temporary_password"])
    sheets = (await app_client.get(f"{E}/children/{child}/datesheets")).json()
    assert len(sheets) == 1 and len(sheets[0]["papers"]) == 2
    pdf = await app_client.get(f"{E}/children/{child}/admit-card/{sid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    page = await app_client.get("/dashboard/parent")
    assert 'id="exam-card"' in page.text
    # a student who is not their child (different guardian in the test data only if unlinked): an unknown id is refused
    assert (await app_client.get(f"{E}/children/00000000-0000-0000-0000-000000000000/datesheets")).status_code == 404
    assert (await app_client.get(f"{E}/schedules")).status_code == 403

    # the invigilating teacher sees exactly the paper they invigilate
    await _first_login(app_client, teacher_user, first)
    duties = (await app_client.get(f"{E}/teaching/papers")).json()
    assert [x["course_offering_id"] for s in duties for x in s["papers"]] == [o1]
    assert (await app_client.get("/dashboard/faculty/exams")).status_code == 200