"""Room list with seats, teacher leave in the free-slot finder, and the card check page for a term not started yet."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.academics.models import Enrollment, EnrollmentStatus
from app.core.clock import institute_today
from app.core.config import settings
from app.records.models import IssuedDocument
from app.timekeeping.models import LeaveRequest
from tests.integration.test_module3_sections_allocation import _DAYS, _admin, _course, _offering, _period, _program, _slot, _student
from tests.integration.test_teacher_content_and_bulk_timetable import _sign_in_teacher, _teacher_session

pytestmark = pytest.mark.asyncio
API = "/api/v1/academics/class-requests"
ROOMS = "/api/v1/academics/rooms"


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def test_rooms_with_seats_and_teacher_leave(app_client, admin_user, db_session):
    await _admin(app_client)
    today = institute_today()
    day = today + timedelta(days=1)
    weekday = _DAYS[day.weekday()]
    prog = await _program(db_session, code="RML")
    oid = await _offering(app_client, await _course(app_client, "RM-101", str(prog.id)), start=today - timedelta(days=14), end=today + timedelta(days=40))
    tid, teacher = await _teacher_session(app_client, db_session, "RMT1", "35202-8400001-1", oid)
    for n in (1, 2):
        st = await _student(db_session, 80 + n, prog)
        db_session.add(Enrollment(student_id=st.id, course_offering_id=uuid.UUID(oid), status=EnrollmentStatus.ENROLLED, enrolled_at=datetime.now(timezone.utc)))
    await db_session.commit()
    p1 = await _period(app_client, "09:00:00", "10:00:00")
    p2 = await _period(app_client, "10:00:00", "11:00:00")

    # no rooms listed: any typed room is accepted (as before)
    assert (await _slot(app_client, oid, p1, "Old Room", day=weekday)).status_code == 201

    small = await app_client.post(ROOMS, json={"name": "Tutorial 1", "kind": "CLASS", "seats": 1})
    assert small.status_code == 201, small.text
    assert (await app_client.post(ROOMS, json={"name": "tutorial1"})).status_code == 409            # same room, other spelling
    big = await app_client.post(ROOMS, json={"name": "Hall A", "kind": "HALL", "seats": 60})
    assert big.status_code == 201
    unknown = await _slot(app_client, oid, p2, "Room 9", day=_DAYS[(day + timedelta(days=1)).weekday()])
    assert unknown.status_code in (409, 422) and "not on the room list" in unknown.text
    tiny = await _slot(app_client, oid, p2, "tutorial 1", day=_DAYS[(day + timedelta(days=1)).weekday()])
    assert tiny.status_code in (409, 422) and "has 1 seats but 2 students" in tiny.text
    assert (await _slot(app_client, oid, p2, "Hall A", day=_DAYS[(day + timedelta(days=1)).weekday()])).status_code == 201

    # free slots offer only listed rooms big enough for the class
    await _sign_in_teacher(app_client, teacher)
    slots = (await app_client.get(f"{API}/offerings/{oid}/free-slots", params={"start": str(day), "days": 1})).json()
    assert slots and all(s["rooms"] == ["Hall A"] for s in slots)

    # approved leave that day: nothing free
    db_session.add(LeaveRequest(employee_id=uuid.UUID(tid), kind="CASUAL", first_day=day, last_day=day, days=1, reason="Family", status="APPROVED"))
    await db_session.commit()
    assert (await app_client.get(f"{API}/offerings/{oid}/free-slots", params={"start": str(day), "days": 1})).json() == []

    # a room taken out of use is not offered any more
    await _admin(app_client)
    hall = big.json()["id"]
    assert (await app_client.patch(f"{ROOMS}/{hall}", json={"is_active": False})).json()["is_active"] is False
    assert (await app_client.patch(f"{ROOMS}/{small.json()['id']}", json={"seats": None})).json()["seats"] is None


async def test_card_check_page_says_not_valid_yet_before_the_term_starts(app_client, admin_user, db_session):
    from tests.integration.test_fees import _create_and_activate_student
    from tests.integration.test_attendance_and_grading import _login_admin

    sid, _ = await _create_and_activate_student(app_client, cnic="75201-0000041-1")
    await _login_admin(app_client)
    assert (await app_client.post("/api/v1/records/id-cards", json={"student_ids": [str(sid)], "employee_ids": []})).status_code == 200
    doc = (await db_session.execute(select(IssuedDocument).where(IssuedDocument.owner_id == uuid.UUID(str(sid)), IssuedDocument.kind == "CARD"))).scalars().one()
    start = institute_today() + timedelta(days=10)
    await db_session.execute(update(IssuedDocument).where(IssuedDocument.id == doc.id).values(
        details={**doc.details, "valid_from": start.isoformat(), "valid_to": (start + timedelta(days=120)).isoformat()}))
    await db_session.commit()
    page = (await app_client.get(f"/verify/{doc.verification_code}")).text
    assert "not valid yet" in page and f"{start:%d %B %Y}" in page
    api = (await app_client.get(f"/api/v1/records/verify/{doc.verification_code}")).json()
    assert api["not_yet"] is True and api["expired"] is False
    _ = date
