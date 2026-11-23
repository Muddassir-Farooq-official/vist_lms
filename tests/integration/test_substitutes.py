"""Substitute teachers (app/substitutes): approved leave -> a free teacher proposed for each class; the office confirms,
changes or cancels; the confirmed substitute is told and marks that one class's attendance, and nothing else."""
from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.academics.models import ClassSession, ClassSessionStatus, CourseOffering
from app.core.clock import institute_now, institute_today
from app.core.config import settings
from app.notifications.models import Notification
from app.substitutes.models import ClassSubstitute
from app.substitutes.service import SubstituteService
from app.timekeeping.models import LeaveRequest
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _login, _login_admin
from tests.integration.test_exam_duty import _emp
from tests.integration.test_result_sheets import _class

pytestmark = pytest.mark.asyncio
S = "/api/v1/substitutes"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def test_leave_proposes_office_confirms_substitute_marks(app_client, admin_user, db_session):
    offering, students, _a, _teacher = await _class(app_client, db_session, code="SUB", n=2)
    await _login_admin(app_client)
    names = {code: await _staff(app_client, db_session, code, "TEACHER", cnic) for code, cnic in (("SUBB", "35202-7110001-1"),)}
    b = await _emp(db_session, "SUBB")
    off = await db_session.get(CourseOffering, uuid.UUID(offering))
    absent = off.assigned_teacher_id
    now = institute_now()
    today = institute_today()
    # The class started five minutes ago (inside the 15-minute marking window) and has not ended. Only in the first
    # minutes after midnight does it start at 00:00 instead, which is still inside the window.
    minutes = now.hour * 60 + now.minute
    start = (datetime.combine(today, now.time()) - timedelta(minutes=5)).time() if minutes >= 5 else time(0, 0)
    end = (datetime.combine(today, now.time()) + timedelta(minutes=40)).time() if minutes < 23 * 60 + 19 else time(23, 59)
    s1 = ClassSession(course_offering_id=off.id, session_date=today, start_time=start, end_time=end, room="R-1", status=ClassSessionStatus.SCHEDULED)
    s2 = ClassSession(course_offering_id=off.id, session_date=today + timedelta(days=1), start_time=time(9), end_time=time(10), room="R-1",
                      status=ClassSessionStatus.SCHEDULED)
    db_session.add_all([s1, s2])
    leave = LeaveRequest(employee_id=absent, kind="CASUAL", first_day=today, last_day=today + timedelta(days=1), days=2, reason="Family", status="APPROVED")
    db_session.add(leave)
    await db_session.flush()
    s1_id, s2_id, b_id, b_user = s1.id, s2.id, b.id, b.user_id

    assert await SubstituteService(db_session).propose_for_leave(leave) == 2
    assert await SubstituteService(db_session).propose_for_leave(leave) == 0                      # each class once
    rows = (await db_session.execute(select(ClassSubstitute).where(ClassSubstitute.leave_request_id == leave.id))).scalars().all()
    assert {r.status for r in rows} == {"PROPOSED"} and all(r.substitute_employee_id not in (None, absent) for r in rows)
    await db_session.commit()

    listed = [x for x in (await app_client.get(S, params={"status": "PROPOSED"})).json() if x["class_session_id"] in (str(s1_id), str(s2_id))]
    assert len(listed) == 2
    cands = (await app_client.get(f"{S}/{s1_id}/candidates")).json()
    assert str(absent) not in [c["employee_id"] for c in cands] and any(c["employee_id"] == str(b_id) and c["free"] for c in cands)
    ok = await app_client.post(f"{S}/{s1_id}", json={"action": "CONFIRM", "employee_id": str(b_id)})
    assert ok.status_code == 200, ok.text
    assert (await app_client.post(f"{S}/{s2_id}", json={"action": "CONFIRM", "employee_id": str(absent)})).status_code == 422   # the absent teacher
    cancel = await app_client.post(f"{S}/{s2_id}", json={"action": "CANCEL", "note": "No one free"})
    assert cancel.status_code == 200
    db_session.expire_all()
    assert (await db_session.get(ClassSession, s2_id)).status == ClassSessionStatus.CANCELLED
    told = (await db_session.execute(select(Notification).where(Notification.user_id == b_user, Notification.title.like("Substitute class:%")))).scalars().all()
    assert len(told) == 1

    # the substitute sees the class and marks it; nothing else
    await _first_login(app_client, *names["SUBB"])
    mine = (await app_client.get(f"{S}/mine")).json()
    assert [m["class_session_id"] for m in mine] == [str(s1_id)]
    roll = (await app_client.get(f"{S}/mine/{s1_id}/students")).json()
    assert len(roll["students"]) == 2
    present = [roll["students"][0]["id"]]
    marked = await app_client.post(f"/api/v1/attendance/sessions/{s1_id}/mark", json={"present_student_ids": present})
    assert marked.status_code == 200, marked.text
    assert (await app_client.post(f"/api/v1/attendance/sessions/{s2_id}/mark", json={"present_student_ids": []})).status_code == 403
    assert (await app_client.get(f"{S}/mine/{s2_id}/students")).status_code == 403
    assert (await app_client.get(S)).status_code == 403                                            # only the administration plans
    page = (await app_client.get("/dashboard/faculty/substitutes")).text
    assert "substitutes-teacher.js" in page
    assert students and _login
