"""A teacher finds a free period (teacher + class + room all free), books a make-up for a missed class or an extra class,
and an administrator approves it: the class is added, the missed one is marked made up, and people are told."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.academics.models import ClassSession, ClassSessionStatus, Enrollment, EnrollmentStatus
from app.audit.models import AuditLog
from app.core.clock import institute_today
from app.core.config import settings
from app.employees.models import Employee
from app.notifications.models import Notification
from tests.integration.test_module3_sections_allocation import _DAYS, _admin, _course, _offering, _period, _program, _slot, _student, _teacher
from tests.integration.test_teacher_content_and_bulk_timetable import _sign_in_teacher, _teacher_session

pytestmark = pytest.mark.asyncio
API = "/api/v1/academics/class-requests"


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def test_free_slots_make_up_and_extra_class_with_approval(app_client, admin_user, db_session):
    await _admin(app_client)
    today = institute_today()
    day = today + timedelta(days=1)                     # the class's weekly day: tomorrow
    weekday = _DAYS[day.weekday()]
    prog = await _program(db_session, code="MKP")
    start, end = today - timedelta(days=21), today + timedelta(days=28)
    mine = await _offering(app_client, await _course(app_client, "MK-101", str(prog.id)), start=start, end=end)
    other = await _offering(app_client, await _course(app_client, "MK-102", str(prog.id)), start=start, end=end)
    tid, teacher = await _teacher_session(app_client, db_session, "MKT1", "35202-8300001-1", mine)
    for n in (1, 2):
        st = await _student(db_session, 60 + n, prog)
        for oid in (mine, other):
            db_session.add(Enrollment(student_id=st.id, course_offering_id=uuid.UUID(oid), status=EnrollmentStatus.ENROLLED, enrolled_at=datetime.now(timezone.utc)))
    await db_session.commit()
    p1 = await _period(app_client, "09:00:00", "10:00:00")
    p2 = await _period(app_client, "10:00:00", "11:00:00")
    p3 = await _period(app_client, "11:00:00", "12:00:00")
    assert (await _slot(app_client, mine, p1, "R-1", day=weekday)).status_code == 201       # own class
    assert (await _slot(app_client, other, p2, "R-2", day=weekday)).status_code == 201      # same students, another subject

    await _sign_in_teacher(app_client, teacher)
    courses = (await app_client.get(f"{API}/my-courses")).json()
    assert [c["id"] for c in courses] == [mine] and courses[0]["missed_classes"] >= 1

    # free: only period 3 (period 1 = own class, period 2 = the same students elsewhere); both rooms free then
    slots = (await app_client.get(f"{API}/offerings/{mine}/free-slots", params={"start": str(day), "days": 1})).json()
    assert [(s["period_id"], s["rooms"]) for s in slots] == [(p3, ["R-1", "R-2"])]

    missed = (await app_client.get(f"{API}/offerings/{mine}/missed")).json()
    assert missed and all(m["why"] == "Attendance not taken" for m in missed)
    target = missed[-1]

    # an extra class needs a reason; a make-up needs a real missed class
    assert (await app_client.post(f"{API}/offerings/{mine}", json={"kind": "EXTRA", "session_date": str(day), "period_id": p3, "room": "R-1"})).status_code == 422
    # a busy period is refused
    busy = await app_client.post(f"{API}/offerings/{mine}", json={"kind": "MAKEUP", "session_date": str(day), "period_id": p2, "room": "R-1",
                                                                   "replaces_session_id": target["id"]})
    assert busy.status_code in (409, 422) and "not free" in busy.text

    r = await app_client.post(f"{API}/offerings/{mine}", json={"kind": "MAKEUP", "session_date": str(day), "period_id": p3, "room": "r-1",
                                                                "replaces_session_id": target["id"], "reason": "Missed for a workshop"})
    assert r.status_code == 200, r.text
    assert r.json()["pending_approval"] is True
    request_id = r.json()["approval_request_id"]
    # the request holds the slot: no free period left that day, and the missed class cannot be asked for twice
    assert (await app_client.get(f"{API}/offerings/{mine}/free-slots", params={"start": str(day), "days": 1})).json() == []
    assert target["id"] not in {m["id"] for m in (await app_client.get(f"{API}/offerings/{mine}/missed")).json()}
    mine_list = (await app_client.get(f"{API}/mine")).json()
    assert mine_list[0]["status"] == "REQUESTED" and "Make-up class" in mine_list[0]["summary"]

    # the administrator approves it
    await _admin(app_client)
    d = await app_client.post(f"/api/v1/approvals/{request_id}/decide", json={"approve": True, "note": "OK"})
    assert d.status_code == 200, d.text
    new_id = d.json()["result"]["class_session_id"]
    db_session.expire_all()
    new = await db_session.get(ClassSession, uuid.UUID(new_id))
    assert new.session_date == day and str(new.start_time) == "11:00:00" and new.room == "R-1" and new.status == ClassSessionStatus.SCHEDULED
    old = await db_session.get(ClassSession, uuid.UUID(target["id"]))
    assert old.status == ClassSessionStatus.CANCELLED and old.cancelled_reason.startswith("Made up on")
    assert (await db_session.execute(select(AuditLog).where(AuditLog.action == "class_session.extra_added"))).scalars().one()
    teacher_user = (await db_session.get(Employee, uuid.UUID(tid))).user_id
    titles = (await db_session.execute(select(Notification.title).where(Notification.user_id == teacher_user))).scalars().all()
    assert "Extra class added" in titles and any("approved" in t for t in titles)

    # an extra class for a course that is behind, then withdrawn by the teacher
    await _sign_in_teacher_again(app_client, teacher)
    day2 = day + timedelta(days=7)
    slots2 = (await app_client.get(f"{API}/offerings/{mine}/free-slots", params={"start": str(day2), "days": 1})).json()
    assert p3 in [s["period_id"] for s in slots2]
    e = await app_client.post(f"{API}/offerings/{mine}", json={"kind": "EXTRA", "session_date": str(day2), "period_id": p3, "room": "R-2",
                                                                "reason": "Two topics behind before the mid-term"})
    assert e.status_code == 200 and e.json()["pending_approval"] is True
    w = await app_client.post(f"/api/v1/approvals/{e.json()['approval_request_id']}/cancel")
    assert w.status_code == 200 and w.json()["status"] == "CANCELLED"
    assert p3 in [s["period_id"] for s in (await app_client.get(f"{API}/offerings/{mine}/free-slots", params={"start": str(day2), "days": 1})).json()]

    # another course's teacher cannot book this one
    await _admin(app_client)
    _t2, teacher2 = await _teacher_session(app_client, db_session, "MKT2", "35202-8300002-2", other)
    await _sign_in_teacher(app_client, teacher2)
    assert (await app_client.get(f"{API}/offerings/{mine}/free-slots")).status_code == 403


async def _sign_in_teacher_again(client, username):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": "TeacherNew12345!"})
    assert r.status_code == 200, r.text
