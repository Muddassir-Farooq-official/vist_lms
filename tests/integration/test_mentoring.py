"""Student mentoring (app/mentoring): the administration shares students out among mentors evenly; only the student's
mentor and administrators read the notes (not other teachers, the student or parents); a referral reaches the
administration and its answer reaches the mentor; students and parents see only the mentor's name."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.employees.models import Employee
from app.notifications.models import Notification
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _create_and_activate_student, _login, _login_admin

pytestmark = pytest.mark.asyncio
M = "/api/v1/mentoring"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


async def test_assign_meet_refer_and_privacy(app_client, admin_user, db_session):
    studs = [await _create_and_activate_student(app_client, cnic=f"35202-91100{i:02d}-1", contact_no=f"0300911{i:04d}") for i in range(3)]
    ids = [s for s, _u in studs]
    await _login_admin(app_client)
    t1, p1 = await _staff(app_client, db_session, "MNT1", "TEACHER", "35202-9120001-1")
    t2, p2 = await _staff(app_client, db_session, "MNT2", "TEACHER", "35202-9120002-1")
    codes = [e.employee_code for e in (await db_session.execute(select(Employee).where(Employee.first_name.in_(["MNT1", "MNT2"])).order_by(Employee.first_name))).scalars()]

    assert (await app_client.post(f"{M}/assign", json={"student_ids": ids, "mentor_codes": ["NOPE-9"]})).status_code == 422
    out = await _ok(await app_client.post(f"{M}/assign", json={"student_ids": ids, "mentor_codes": codes}))
    assert out["assigned"] == 3
    ov = await _ok(await app_client.get(f"{M}/overview"))
    loads = sorted(m["mentees"] for m in ov["mentors"])
    assert loads == [1, 2]                                                  # shared out evenly
    again = await _ok(await app_client.post(f"{M}/assign", json={"student_ids": ids, "mentor_codes": codes[:1]}))
    assert again["assigned"] == 0 and again["unchanged"] == 3              # kept: they already have a mentor
    await _ok(await app_client.post(f"{M}/assign", json={"student_ids": ids, "mentor_codes": codes[:1], "only_unassigned": False}))
    ov = await _ok(await app_client.get(f"{M}/overview"))
    assert [m["mentees"] for m in ov["mentors"]] == [3]                    # all moved to the first mentor

    # the mentor records a meeting and refers a student
    pw1 = await _first_login(app_client, t1, p1)
    mine = await _ok(await app_client.get(f"{M}/mentees"))
    assert {x["id"] for x in mine} == set(ids)
    assert (await app_client.post(f"{M}/students/{ids[0]}/meetings", json={"notes": "x"})).status_code == 422
    assert (await app_client.post(f"{M}/students/{ids[0]}/meetings", json={"notes": "Struggling with pharmacology.", "refer": True})).status_code == 422
    meet = await _ok(await app_client.post(f"{M}/students/{ids[0]}/meetings", json={
        "notes": "Struggling with pharmacology; family problems at home.", "actions": "Extra class on Saturdays", "follow_up_on": "2099-01-01",
        "refer": True, "referral_reason": "Needs fee concession and counselling"}), 201)
    f = await _ok(await app_client.get(f"{M}/students/{ids[0]}"))
    assert f["meetings"][0]["notes"].startswith("Struggling") and f["meetings"][0]["referral_status"] == "OPEN"
    assert (await app_client.get(f"{M}/overview")).status_code == 403

    # another teacher, the student and a parent cannot read it
    await _first_login(app_client, t2, p2)
    assert (await app_client.get(f"{M}/students/{ids[0]}")).status_code == 403
    assert (await app_client.post(f"{M}/students/{ids[0]}/meetings", json={"notes": "Not my mentee at all"})).status_code == 403
    assert await _ok(await app_client.get(f"{M}/mentees")) == []
    await _login(app_client, studs[0][1], "StudentPass123!")
    assert (await app_client.get(f"{M}/students/{ids[0]}")).status_code == 403
    card = (await _ok(await app_client.get("/api/v1/family/cards")))[0]
    assert card["mentor"] == "MNT1 X" and "notes" not in str(card).lower()

    # the administration reads and answers the referral; the mentor is told
    await _login_admin(app_client)
    refs = await _ok(await app_client.get(f"{M}/referrals"))
    assert [r["id"] for r in refs] == [meet["id"]] and refs[0]["notes"].startswith("Struggling")
    await _ok(await app_client.post(f"{M}/referrals/{meet['id']}/resolve", json={"resolution": "50% concession approved; counsellor booked."}))
    assert (await app_client.post(f"{M}/referrals/{meet['id']}/resolve", json={"resolution": "again again"})).status_code == 422
    assert (await _ok(await app_client.get(f"{M}/students/{ids[0]}")))["meetings"][0]["resolution"].startswith("50%")     # admins read the file
    db_session.expire_all()
    titles = (await db_session.execute(select(Notification.title).where(Notification.title.like("%referral%")))).scalars().all()
    assert any(t.startswith("Mentor referral") for t in titles) and "Your referral was answered" in titles

    # the mentor corrects, then hides, a note; ending the mentor keeps the notes
    await _login(app_client, t1, pw1)
    await _ok(await app_client.put(f"{M}/meetings/{meet['id']}", json={"actions": "Extra class on Saturdays and Sundays"}))
    await _login_admin(app_client)
    await _ok(await app_client.delete(f"{M}/students/{ids[0]}/mentor"))
    f = await _ok(await app_client.get(f"{M}/students/{ids[0]}"))
    assert f["mentor"] is None and f["meetings"][0]["actions"].endswith("Sundays")


async def test_pages_render(app_client, admin_user):
    await _login_admin(app_client)
    for path in ("/dashboard/admin/mentoring", "/dashboard/faculty/mentees"):
        assert (await app_client.get(path)).status_code == 200, path
