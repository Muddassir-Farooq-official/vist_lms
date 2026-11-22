"""Committees (app/committees): only members and administrators see a committee's meetings and minutes; the chair or
secretary schedules and writes them; the chair makes them final (locked); actions notify and remind their owner once a
day until done; a student cannot be a member."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.committees.service import remind_actions
from app.core.clock import institute_today
from app.core.config import settings
from app.employees.models import Employee
from app.notifications.models import Notification
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _create_and_activate_student, _login, _login_admin

pytestmark = pytest.mark.asyncio
C = "/api/v1/committees"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


async def test_committee_meeting_minutes_and_actions(app_client, admin_user, db_session):
    _sid, student_user = await _create_and_activate_student(app_client, cnic="35202-9310001-1", contact_no="03009310001")
    await _login_admin(app_client)
    people = {}
    for i, code in enumerate(("CHR", "SEC", "MBR", "OUT")):
        people[code] = await _staff(app_client, db_session, code, "TEACHER", f"35202-93200{i:02d}-1")
    codes = {e.first_name: e.employee_code for e in (await db_session.execute(select(Employee).where(Employee.first_name.in_(list(people))))).scalars()}

    one_chair = [{"employee_code": codes["CHR"], "role": "CHAIR"}, {"employee_code": codes["SEC"], "role": "SECRETARY"}, {"employee_code": codes["MBR"]}]
    assert (await app_client.post(C, json={"name": "Academic Council", "members": one_chair[1:]})).status_code == 422          # needs a chair
    com = (await _ok(await app_client.post(C, json={"name": "Academic Council", "purpose": "Curriculum", "members": one_chair}), 201))["id"]
    assert (await app_client.post(C, json={"name": "academic council", "members": one_chair})).status_code == 409

    pw = {k: await _first_login(app_client, u, p) for k, (u, p) in people.items()}

    # the secretary schedules; members are told; a plain member cannot schedule
    await _login(app_client, people["MBR"][0], pw["MBR"])
    assert (await app_client.post(f"{C}/{com}/meetings", json={"held_on": str(institute_today()), "agenda": "Exams policy"})).status_code == 403
    await _login(app_client, people["SEC"][0], pw["SEC"])
    mt = (await _ok(await app_client.post(f"{C}/{com}/meetings", json={"held_on": str(institute_today()), "start_time": "10:00", "place": "Board room",
                                                                         "agenda": "1. Exams policy\n2. Clinical hours"}), 201))["id"]
    await _ok(await app_client.put(f"{C}/meetings/{mt}", json={"minutes": "The council discussed the exams policy at length.", "decisions": "Policy approved.",
                                                                "attended": "CHR, SEC, MBR"}))
    act = (await _ok(await app_client.post(f"{C}/meetings/{mt}/actions", json={"title": "Circulate the exams policy", "owner_code": codes["OUT"],
                                                                               "due_on": str(institute_today() + timedelta(days=1))}), 201))["id"]
    assert (await app_client.post(f"{C}/meetings/{mt}/final")).status_code == 403                                   # only the chair

    # an outsider owns an action but cannot read the committee
    await _login(app_client, people["OUT"][0], pw["OUT"])
    assert await _ok(await app_client.get(C)) == []
    assert (await app_client.get(f"{C}/meetings/{mt}")).status_code == 403
    assert (await app_client.get(f"{C}/meetings/{mt}/pdf")).status_code == 403
    mine = await _ok(await app_client.get(f"{C}/actions/mine"))
    assert [a["id"] for a in mine] == [act] and mine[0]["committee"] == "Academic Council"
    assert (await app_client.post(f"{C}/actions/{act}", json={"status": "DROPPED"})).status_code == 403           # only chair/secretary drop

    # reminders: once a day from 9 AM
    db_session.expire_all()
    nine = datetime.combine(institute_today(), datetime.min.time()).replace(tzinfo=timezone.utc) + timedelta(hours=10)
    assert await remind_actions(db_session, now=nine) == 1
    assert await remind_actions(db_session, now=nine) == 0
    await db_session.commit()

    await _ok(await app_client.post(f"{C}/actions/{act}", json={"status": "DONE", "note": "Emailed to all staff"}))

    # a student cannot see anything; the chair makes the minutes final; they lock
    await _login(app_client, student_user, "StudentPass123!")
    assert await _ok(await app_client.get(C)) == []
    assert (await app_client.get(f"{C}/meetings/{mt}")).status_code == 403
    await _login(app_client, people["CHR"][0], pw["CHR"])
    d = await _ok(await app_client.get(f"{C}/meetings/{mt}"))
    assert d["can_finalise"] and d["actions"][0]["status"] == "DONE"
    await _ok(await app_client.post(f"{C}/meetings/{mt}/final"))
    assert (await app_client.put(f"{C}/meetings/{mt}", json={"minutes": "Changed after the fact, which is not allowed"})).status_code == 422
    pdf = await app_client.get(f"{C}/meetings/{mt}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"

    db_session.expire_all()
    titles = (await db_session.execute(select(Notification.title).where(Notification.title.like("Academic Council:%")))).scalars().all()
    assert any("meeting on" in t for t in titles) and any("an action for you" in t for t in titles) and any("due tomorrow" in t for t in titles)
    assert any("minutes of" in t for t in titles)

    # the administration sees everything; a student login cannot be made a member
    await _login_admin(app_client)
    assert [c["name"] for c in await _ok(await app_client.get(C))] == ["Academic Council"]
    from app.students.models import Student

    st = (await db_session.execute(select(Student).where(Student.user_id.is_not(None)))).scalars().first()
    bad = await app_client.put(f"{C}/{com}", json={"name": "Academic Council", "members": [{"user_id": str(st.user_id), "role": "CHAIR"}]})
    assert bad.status_code == 422 and "student or parent" in bad.text


async def test_my_committees_link_goes_to_own_portal(app_client, admin_user):
    await _login_admin(app_client)
    r = await app_client.get("/dashboard/my-committees", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/dashboard/admin/committees"
    for portal in ("admin", "faculty", "staff", "accounts", "exam"):
        assert (await app_client.get(f"/dashboard/{portal}/committees")).status_code == 200, portal
    assert (await app_client.get("/dashboard/student/committees")).status_code == 404
