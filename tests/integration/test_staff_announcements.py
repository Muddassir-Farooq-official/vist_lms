"""Teachers and staff write announcements; the admin approves or sends them back; audiences by section / program / semester."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.academics.models import CourseOffering
from app.core.clock import institute_today
from app.core.config import settings
from app.employees.models import Employee
from app.notifications.models import Notification
from app.students.models import Student
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _login, _login_admin, _setup_offering_with_enrolled_students

pytestmark = pytest.mark.asyncio
API = "/api/v1/announcements"
PW = "StudentPass123!"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _teacher_with_section(app_client, db_session, code, cnic):
    off, studs = await _setup_offering_with_enrolled_students(app_client, code=code, n_students=1, cnic_base=int(cnic[6:10]))
    await _login_admin(app_client)
    t_user, t_first = await _staff(app_client, db_session, code + "T", "TEACHER", cnic)
    emp = (await db_session.execute(select(Employee).where(Employee.first_name == code + "T"))).scalar_one()
    await db_session.execute(update(CourseOffering).where(CourseOffering.id == uuid.UUID(off))
                             .values(assigned_teacher_id=emp.id, term_end_date=institute_today() + timedelta(days=60)))   # a current term
    await db_session.commit()
    pw = await _first_login(app_client, t_user, t_first)
    return off, studs[0], (t_user, pw), emp


async def _titles(app_client, username, password):
    await _login(app_client, username, password)
    return [a["title"] for a in (await app_client.get(API)).json()]


async def test_a_teacher_announces_to_their_section_after_the_admin_approves(app_client, admin_user, db_session):
    off, (sid, s_user, _), (t_user, t_pw), emp = await _teacher_with_section(app_client, db_session, "SA1", "35202-4910001-1")
    _other_off, other = await _setup_offering_with_enrolled_students(app_client, code="SA2", n_students=1, cnic_base=4920)
    o_user = other[0][1]

    await _login(app_client, t_user, t_pw)
    form = (await app_client.get(f"{API}/mine")).json()
    assert form["issued_as"] == {"name": emp.full_name, "designation": "SA1T"}               # from the account, not typed
    assert [s["id"] for s in form["targets"]["sections"]] == [off] and form["targets"]["sections_are_own"] is True
    bad = await app_client.post(f"{API}/mine", json={"title": "Quiz", "body": "x", "offering_ids": [_other_off]})
    assert bad.status_code == 422 and "sections you teach" in bad.text                         # not someone else's section
    r = await app_client.post(f"{API}/mine", json={"title": "Quiz on Monday", "body": "Chapter 3. Bring a calculator.", "priority": "IMPORTANT",
                                                  "offering_ids": [off]})
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    assert r.json()["review"] == "PENDING" and r.json()["audience_text"] == "Students of SA1-C1 A"

    assert "Quiz on Monday" not in await _titles(app_client, s_user, PW)                      # invisible until approved

    await _login_admin(app_client)
    waiting = (await app_client.get(f"{API}/manage", params={"review": "PENDING"})).json()
    assert [a["id"] for a in waiting] == [aid] and waiting[0]["issuer_name"] == emp.full_name and waiting[0]["submitted_by_name"]
    note = (await db_session.execute(select(Notification).where(Notification.title == "Announcement to approve"))).scalars().first()
    assert note is not None and note.link == "/dashboard/admin/announcements"
    ok = await app_client.post(f"{API}/{aid}/approve")
    assert ok.status_code == 200 and ok.json()["status"] == "PUBLISHED" and ok.json()["review"] == "APPROVED"
    assert (await app_client.post(f"{API}/{aid}/approve")).status_code in (400, 409, 422)

    assert "Quiz on Monday" in await _titles(app_client, s_user, PW)                          # in the section
    assert "Quiz on Monday" not in await _titles(app_client, o_user, PW)                      # another section
    told = (await db_session.execute(select(Notification).where(Notification.title == "Your announcement is published"))).scalars().all()
    assert len(told) == 1 and told[0].link == "/dashboard/my-announcements"
    s = (await db_session.execute(select(Student).where(Student.id == uuid.UUID(sid)))).scalar_one()
    student_bell = (await db_session.execute(select(Notification).where(Notification.user_id == s.user_id, Notification.title.like("%Quiz on Monday")))).scalars().all()
    assert len(student_bell) == 1

    await _login(app_client, t_user, t_pw)
    mine = (await app_client.get(f"{API}/mine")).json()["announcements"]
    assert mine[0]["review"] == "APPROVED" and mine[0]["can_edit"] is False
    assert (await app_client.patch(f"{API}/mine/{aid}", json={"title": "Changed", "body": "x"})).status_code in (400, 409, 422)
    page = await app_client.get("/dashboard/faculty/my-announcements", headers={"accept": "text/html"})
    assert page.status_code == 200 and "my-announcements.js" in page.text
    go = await app_client.get("/dashboard/my-announcements", follow_redirects=False)
    assert go.status_code == 302 and go.headers["location"] == "/dashboard/faculty/my-announcements"


async def test_rejected_then_fixed_then_approved_and_program_semester_audience(app_client, admin_user, db_session):
    off, (sid, s_user, _), (t_user, t_pw), _emp = await _teacher_with_section(app_client, db_session, "SB1", "35202-4930001-1")
    s = (await db_session.execute(select(Student).where(Student.id == uuid.UUID(sid)))).scalar_one()
    await _login(app_client, t_user, t_pw)
    r = await app_client.post(f"{API}/mine", json={"title": "Trip", "body": "Trip to the museum.", "program_ids": [str(s.program_id)], "semesters": [2]})
    aid = r.json()["id"]
    assert r.json()["audience_text"] == "SB1 students · semester 2"

    await _login_admin(app_client)
    assert (await app_client.post(f"{API}/{aid}/reject", json={"note": "x"})).status_code == 422
    rej = await app_client.post(f"{API}/{aid}/reject", json={"note": "Add the date and the fee."})
    assert rej.status_code == 200 and rej.json()["review"] == "REJECTED"
    assert aid not in [a["id"] for a in (await app_client.get(f"{API}/manage")).json()]        # not in the admin's list any more

    await _login(app_client, t_user, t_pw)
    mine = (await app_client.get(f"{API}/mine")).json()["announcements"][0]
    assert mine["review"] == "REJECTED" and mine["review_note"] == "Add the date and the fee." and mine["can_edit"]
    fixed = await app_client.patch(f"{API}/mine/{aid}", json={"title": "Trip on 20 Nov", "body": "Trip to the museum, Rs. 500.",
                                                             "program_ids": [str(s.program_id)], "semesters": [1, 2]})
    assert fixed.status_code == 200 and fixed.json()["review"] == "PENDING"

    await _login_admin(app_client)
    assert (await app_client.post(f"{API}/{aid}/publish")).json()["review"] == "APPROVED"     # publishing a waiting one approves it
    assert "Trip on 20 Nov" in await _titles(app_client, s_user, PW)                          # semester 1 of the program
    await db_session.execute(update(Student).where(Student.id == s.id).values(current_semester=3))
    await db_session.commit()
    assert "Trip on 20 Nov" not in await _titles(app_client, s_user, PW)                      # not in semester 3


async def test_staff_can_write_to_colleagues_and_take_one_back(app_client, admin_user, db_session):
    await _login_admin(app_client)
    a_user, a_first = await _staff(app_client, db_session, "ACCSA", "ACCOUNTS_OFFICER", "35202-4940001-1")
    t_user, t_first = await _staff(app_client, db_session, "TCHSA", "TEACHER", "35202-4950001-1")
    a_pw = await _first_login(app_client, a_user, a_first)
    t_pw = await _first_login(app_client, t_user, t_first)

    await _login(app_client, a_user, a_pw)
    assert (await app_client.post(f"{API}/mine", json={"title": "Nobody", "body": "x", "audience_students": False})).status_code == 422
    r = await app_client.post(f"{API}/mine", json={"title": "Salary slips are out", "body": "Collect from accounts.", "audience_students": False,
                                                  "audience_teachers": True})
    aid = r.json()["id"]
    second = (await app_client.post(f"{API}/mine", json={"title": "Draft I will take back", "body": "x", "audience_students": False, "audience_staff": True})).json()["id"]
    assert (await app_client.post(f"{API}/mine/{second}/cancel")).json()["review"] == "CANCELLED"
    assert (await app_client.post(f"{API}/mine/{second}/cancel")).status_code in (400, 409, 422)

    await _login_admin(app_client)
    assert [a["id"] for a in (await app_client.get(f"{API}/manage", params={"review": "PENDING"})).json()] == [aid]
    await app_client.post(f"{API}/{aid}/approve")
    assert "Salary slips are out" in await _titles(app_client, t_user, t_pw)

    # students cannot write announcements
    _off, studs = await _setup_offering_with_enrolled_students(app_client, code="SA9", n_students=1, cnic_base=4960)
    await _login(app_client, studs[0][1], PW)
    assert (await app_client.post(f"{API}/mine", json={"title": "Hello", "body": "x"})).status_code == 403


async def test_student_home_shows_latest_announcements_first_then_numbers_alerts_and_today(app_client, admin_user):
    _off, studs = await _setup_offering_with_enrolled_students(app_client, code="SA8", n_students=1, cnic_base=4970)
    await _login(app_client, studs[0][1], PW)
    page = (await app_client.get("/dashboard/student", headers={"accept": "text/html"})).text
    order = [page.index(x) for x in ('id="news-cards"', 'id="k-att"', 'id="attn-list"', 'id="today-classes"', 'id="class-list"')]
    assert order == sorted(order)
    assert 'href="/dashboard/student/announcements" class="view-all-link">View all' in page and 'id="ann-list"' not in page
