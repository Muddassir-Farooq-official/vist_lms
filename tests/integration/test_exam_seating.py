"""Exam seating and room invigilation (app/exams/seating.py): rooms with rows x columns, papers mixed in neighbouring
columns, capacity checked, invigilators proposed fairly (free, never the paper's own teacher), the room's teachers open
the roll, admit cards show the seat, and the printouts."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.academics.models import CourseOffering
from app.academics.rooms import Room
from app.core.clock import institute_today
from app.core.config import settings
from app.exams.seating import ExamRoomDuty, ExamSeat, arrange
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _login_admin, _setup_offering_with_enrolled_students
from tests.integration.test_exam_duty import _add, _current, _emp, _schedule

pytestmark = pytest.mark.asyncio
E = "/api/v1/exams"
SE = "/api/v1/exams/seating"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


def test_neighbouring_columns_get_different_papers():
    a, b = "A", "B"
    placed = arrange([("R1", 2, 3)], [(a, ["a1", "a2", "a3", "a4"]), (b, ["b1", "b2"])])
    cols = {}
    for pid, _s, _r, row, col in placed:
        cols.setdefault(col, set()).add(pid)
    assert cols == {1: {a}, 2: {b}, 3: {a}} and len(placed) == 6
    # one paper alone: roll-number order, column by column
    one = arrange([("R1", 2, 2)], [(a, ["a1", "a2", "a3"])])
    assert [(s, r, c) for _p, s, _room, r, c in one] == [("a1", 1, 1), ("a2", 2, 1), ("a3", 1, 2)]


async def test_seat_invigilate_print_and_admit_card(app_client, admin_user, db_session, monkeypatch):
    monkeypatch.setattr(settings, "exam_students_per_invigilator", 2)
    await _login_admin(app_client)
    o1, s1 = await _setup_offering_with_enrolled_students(app_client, code="SE1", n_students=3, cnic_base=5600)
    o2, s2 = await _setup_offering_with_enrolled_students(app_client, code="SE2", n_students=2, cnic_base=5610)
    await _current(db_session, o1, o2)
    await _login_admin(app_client)
    names = {}
    for code, cnic in (("SEA", "35202-5620001-1"), ("SEB", "35202-5630001-1"), ("SEC", "35202-5640001-1")):
        names[code] = await _staff(app_client, db_session, code, "TEACHER", cnic)
    a, b, c = [await _emp(db_session, x) for x in ("SEA", "SEB", "SEC")]
    await db_session.execute(update(CourseOffering).where(CourseOffering.id == uuid.UUID(o1)).values(assigned_teacher_id=a.id))  # A teaches SE1
    hall = Room(name="Exam Hall 1", key="examhall1", kind="CLASS", seats=40)
    small = Room(name="Exam Room 2", key="examroom2", kind="CLASS", seats=4)
    db_session.add_all([hall, small])
    await db_session.commit()

    # rooms: rows x columns
    assert (await app_client.put(f"{SE}/rooms/{hall.id}", json={"exam_rows": 2, "exam_cols": 2})).status_code == 200
    assert (await app_client.put(f"{SE}/rooms/{small.id}", json={"exam_rows": 1})).status_code == 422          # both or neither
    assert (await app_client.put(f"{SE}/rooms/{small.id}", json={"exam_rows": 1, "exam_cols": 2})).status_code == 200

    day = institute_today() + timedelta(days=4)
    sid = await _schedule(app_client, o1)
    p1 = await _add(app_client, sid, o1, day)
    p2 = await _add(app_client, sid, o2, day, room="Hall B")
    slots = (await app_client.get(f"{SE}/{sid}")).json()
    slot = slots["slots"][0]
    assert slot["students"] == 5 and slot["seated"] == 0 and len(slot["papers"]) == 2

    # too few seats in one room: refused; both rooms: everyone seated, papers mixed by column
    few = await app_client.post(f"{SE}/{sid}/seat", json={"exam_date": day.isoformat(), "start_time": "09:00", "room_ids": [str(hall.id)]})
    assert few.status_code == 422 and "only 4 seats" in few.text
    ok = await app_client.post(f"{SE}/{sid}/seat", json={"exam_date": day.isoformat(), "start_time": "09:00", "room_ids": [str(hall.id), str(small.id)]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["seated"] == 5 and ok.json()["rooms"] == ["Exam Hall 1", "Exam Room 2"]
    seats = (await db_session.execute(select(ExamSeat).where(ExamSeat.schedule_id == uuid.UUID(sid)))).scalars().all()
    in_hall = {}
    for x in seats:
        if x.room_id == hall.id:
            in_hall.setdefault(x.seat_col, set()).add(str(x.paper_id))
    assert in_hall[1] != in_hall[2]                                                     # neighbours write different papers
    db_session.expire_all()
    detail = (await app_client.get(f"{E}/schedules/{sid}")).json()
    assert next(p for p in detail["papers"] if p["id"] == p1["id"])["room"] in ("Exam Hall 1", "Exam Hall 1, Exam Room 2")
    plan = (await app_client.get(f"{SE}/{sid}/plan", params={"exam_date": day.isoformat(), "start_time": "09:00"})).json()
    assert [r["name"] for r in plan["rooms"]] == ["Exam Hall 1", "Exam Room 2"] and plan["rooms"][0]["needed"] == 2
    again = await app_client.post(f"{SE}/{sid}/seat", json={"exam_date": day.isoformat(), "start_time": "09:00", "room_ids": [str(hall.id), str(small.id)]})
    assert again.status_code == 200 and len((await db_session.execute(select(ExamSeat).where(ExamSeat.schedule_id == uuid.UUID(sid)))).scalars().all()) == 5

    # invigilators: 1 per 2 students, never SE1's own teacher where SE1 sits, fair spread
    auto = await app_client.post(f"{SE}/{sid}/invigilators/auto", json={"exam_date": day.isoformat(), "start_time": "09:00"})
    assert auto.status_code == 200, auto.text
    duties = (await db_session.execute(select(ExamRoomDuty).where(ExamRoomDuty.schedule_id == uuid.UUID(sid)))).scalars().all()
    hall_people = {d.employee_id for d in duties if d.room_id == hall.id}
    assert a.id not in hall_people and len(hall_people) == 2
    assert sum(d.role.value == "IN_CHARGE" for d in duties if d.room_id == hall.id) == 1
    # editing: one teacher cannot be in two rooms at once
    other = next(d.employee_id for d in duties if d.room_id == hall.id)
    clash = await app_client.put(f"{SE}/{sid}/invigilators", json={"exam_date": day.isoformat(), "start_time": "09:00", "room_id": str(small.id),
                                                                     "employee_ids": [str(other)]})
    assert clash.status_code == 422 and "another room" in clash.text
    set_ok = await app_client.put(f"{SE}/{sid}/invigilators", json={"exam_date": day.isoformat(), "start_time": "09:00", "room_id": str(small.id),
                                                                      "employee_ids": [str(a.id)], "in_charge_id": str(a.id)})
    assert set_ok.status_code == 200
    told = await app_client.post(f"{SE}/{sid}/tell")
    assert told.status_code == 200 and told.json()["teachers"] >= 2
    assert (await app_client.post(f"{SE}/{sid}/tell")).json()["duties"] == 0                       # each duty told once

    # printouts
    for path in ("door-lists.pdf", "attendance-sheets.pdf"):
        r = await app_client.get(f"{SE}/{sid}/{path}", params={"exam_date": day.isoformat(), "start_time": "09:00"})
        assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert (await app_client.get(f"{SE}/{sid}/duty-sheet.pdf")).content.startswith(b"%PDF")

    # published: the admit card shows the seat; A (room duty in Room 2) opens the roll of a paper seated there
    assert (await app_client.post(f"{E}/schedules/{sid}/publish")).status_code == 200
    student = uuid.UUID(s2[0][0])
    card = (await app_client.get(f"{E}/schedules/{sid}/admit-cards/{student}/pdf"))
    assert card.status_code == 200
    from app.exams.service import ExamScheduleService

    data = await ExamScheduleService(db_session).admit_card(uuid.UUID(sid), student)
    assert data["papers"][0].get("seat", "").startswith("R")
    small_papers = {str(x.paper_id) for x in seats if x.room_id == small.id}
    await _first_login(app_client, *names["SEA"])
    mine = (await app_client.get(f"{E}/teaching/duties")).json()
    assert {d["paper_id"] for d in mine} >= small_papers
    pid = next(iter(small_papers))
    assert (await app_client.get(f"{E}/papers/{pid}/roll")).status_code == 200
    assert (await app_client.get(f"{SE}/{sid}")).status_code == 403                                 # teachers do not plan seating
