"""Daily periods: types (class, zero, break, library/self-study), days they apply to, and changing a period's times
after the timetable is built (classes move from a date; later periods shift; recorded classes stay)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.academics.models import ClassSession, TimetableSlot
from tests.integration.test_attendance_and_grading import TERM_END, TERM_START, _login_admin

pytestmark = pytest.mark.asyncio
A = "/api/v1/academics"


async def _period(client, n, start, end, **kw):
    r = await client.post(f"{A}/periods", json={"period_number": n, "start_time": start, "end_time": end, **kw})
    assert r.status_code == 201, r.text
    return r.json()


async def _offering(client, code="PER-101", term="Fall 2026"):
    cid = (await client.post(f"{A}/courses", json={"code": code, "title": "Periods", "credit_hours": 3})).json()["id"]
    r = await client.post(f"{A}/offerings", json={"course_id": cid, "term_label": term, "section_label": "A",
                                                 "term_start_date": str(TERM_START), "term_end_date": str(TERM_END)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_break_and_day_limited_periods_and_12_hour_rows(app_client, admin_user, db_session):
    await _login_admin(app_client)
    p1 = await _period(app_client, 1, "08:00", "09:00")
    brk = await _period(app_client, 2, "09:00", "09:30", kind="BREAK", label="Tea break")
    fri = await _period(app_client, 3, "09:30", "10:10", days=["friday"])
    assert brk["kind"] == "BREAK" and brk["label"] == "Tea break" and fri["days"] == ["FRIDAY"]
    # Friday's own Period 3 and a Monday-Thursday Period 3 at other times can both exist; the same number on the same day cannot
    await _period(app_client, 3, "09:30", "10:30", days=["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY"])
    dup = await app_client.post(f"{A}/periods", json={"period_number": 3, "start_time": "14:00", "end_time": "15:00", "days": ["FRIDAY"]})
    assert dup.status_code in (409, 422)
    overlap = await app_client.post(f"{A}/periods", json={"period_number": 9, "start_time": "08:30", "end_time": "09:10"})
    assert overlap.status_code == 422 and "overlaps" in overlap.text

    off = await _offering(app_client)
    slot = lambda period, day: app_client.post(f"{A}/offerings/{off}/timetable-slots", json={"day_of_week": day, "period_id": period, "room": "R1"})
    no_break = await slot(brk["id"], "MONDAY")
    assert no_break.status_code == 422 and "break" in no_break.text.lower()
    not_monday = await slot(fri["id"], "MONDAY")
    assert not_monday.status_code == 422 and "not used on Monday" in not_monday.text
    assert (await slot(fri["id"], "FRIDAY")).status_code == 201
    assert (await slot(p1["id"], "MONDAY")).status_code == 201

    week = (await app_client.get(f"{A}/timetable/weekly", params={"term_label": "Fall 2026"})).json()
    labels = [r["label"] for r in week["rows"]]
    assert "8:00 AM–9:00 AM" in labels
    tea = next(r for r in week["rows"] if r["label"] == "9:00 AM–9:30 AM")
    assert tea["breaks"]["MONDAY"] == "Tea break" and tea["period_number"] is None

    # a period with classes cannot become a break
    refused = await app_client.post(f"{A}/periods/{p1['id']}/change", json={"start_time": "08:00", "end_time": "09:00", "kind": "BREAK"})
    assert refused.status_code == 422 and "class(es) are timetabled" in refused.text


async def test_lengthening_a_period_moves_its_classes_from_a_date_and_shifts_later_periods(app_client, admin_user, db_session):
    await _login_admin(app_client)
    await _period(app_client, 1, "08:00", "09:00")
    p2 = await _period(app_client, 2, "09:00", "10:00")
    brk = await _period(app_client, 3, "10:00", "10:30", kind="BREAK", label="Lunch")
    p4 = await _period(app_client, 4, "10:30", "11:30")
    off = await _offering(app_client)
    other = await _offering(app_client, code="PER-102")
    assert (await app_client.post(f"{A}/offerings/{off}/timetable-slots", json={"day_of_week": "MONDAY", "period_id": p2["id"], "room": "R1"})).status_code == 201
    assert (await app_client.post(f"{A}/offerings/{other}/timetable-slots", json={"day_of_week": "MONDAY", "period_id": p4["id"], "room": "R1"})).status_code == 201

    from_date = TERM_START + timedelta(weeks=4)
    # one class after that date already has attendance: it must keep its recorded time
    taken = (await db_session.execute(select(ClassSession).where(ClassSession.course_offering_id == uuid.UUID(off),
                                                                 ClassSession.session_date == from_date + timedelta(weeks=1)))).scalar_one()
    taken.attendance_taken_at = datetime.now(timezone.utc)
    await db_session.commit()

    body = {"start_time": "09:00", "end_time": "10:30", "from_date": str(from_date)}
    no_shift = await app_client.post(f"{A}/periods/{p2['id']}/change", json={**body, "shift_later": False})
    assert no_shift.status_code == 422 and "overlap" in no_shift.text

    preview = (await app_client.post(f"{A}/periods/{p2['id']}/change", json=body)).json()
    assert preview["applied"] is False
    assert [(c["period_number"], c["to"]) for c in preview["changes"]] == [
        (2, "9:00 AM–10:30 AM"), (3, "10:30 AM–11:00 AM"), (4, "11:00 AM–12:00 PM")]
    assert preview["kept_with_attendance"] == 1 and preview["day_ends"] == "12:00 PM"
    db_session.expire_all()
    assert (await db_session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id == uuid.UUID(off)))).scalar_one().end_time.hour == 10

    done = (await app_client.post(f"{A}/periods/{p2['id']}/change", json={**body, "apply": True})).json()
    assert done["applied"] is True and done["slots"] == 2
    db_session.expire_all()
    s2 = (await db_session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id == uuid.UUID(off)))).scalar_one()
    s4 = (await db_session.execute(select(TimetableSlot).where(TimetableSlot.course_offering_id == uuid.UUID(other)))).scalar_one()
    assert (str(s2.start_time), str(s2.end_time)) == ("09:00:00", "10:30:00")
    assert (str(s4.start_time), str(s4.end_time)) == ("11:00:00", "12:00:00")
    sessions = (await db_session.execute(select(ClassSession).where(ClassSession.course_offering_id == uuid.UUID(off)))).scalars().all()
    for x in sessions:
        if x.attendance_taken_at is not None:
            assert str(x.end_time) == "10:00:00"                     # recorded class untouched
        elif x.session_date < from_date:
            assert str(x.end_time) == "10:00:00"                     # before the date: as it was
        else:
            assert str(x.end_time) == "10:30:00"
    periods = {p["period_number"]: p for p in (await app_client.get(f"{A}/periods")).json()}
    assert periods[3]["start_time"] == "10:30:00" and periods[3]["label"] == "Lunch" and periods[3]["id"] == brk["id"]
