"""The exam department: its own login, with the result work of the administration and nothing else."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.auth.redirects import home_for_roles
from app.core.config import settings
from app.main import EXAM_NAV_ITEMS
from app.notifications.models import Notification
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _login, _login_admin
from tests.integration.test_result_sheets import _as_teacher, _class, _fill, _mark

pytestmark = pytest.mark.asyncio
R = "/api/v1/results"
A = "/api/v1/academics"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _exam_user(client, db):
    await _login_admin(client)
    username, first = await _staff(client, db, "EXC", "EXAM_CONTROLLER", "35202-7700001-1")
    return username, await _first_login(client, username, first)


async def _as_exam(client, creds):
    await _login(client, creds[0], creds[1])


def test_the_exam_department_has_its_own_login_destination():
    assert home_for_roles(["EXAM_CONTROLLER"]) == "/dashboard/exam/home"                         # their dashboard
    assert home_for_roles(["EXAM_CONTROLLER", "ADMIN"]) == "/dashboard/admin"                      # the most privileged portal still wins
    assert [i["slug"] for i in EXAM_NAV_ITEMS][:3] == ["home", "results", "result-types"] and EXAM_NAV_ITEMS[1]["href"] == "/dashboard/exam"


async def test_the_exam_department_runs_the_whole_result_process(app_client, admin_user, db_session):
    exam = await _exam_user(app_client, db_session)
    await _login_admin(app_client)
    offering, students, (c1, c2), teacher = await _class(app_client, db_session, code="EX1", n=2, cnic_base=7000)
    await _as_teacher(app_client, teacher)
    await _fill(app_client, students, (c1, c2), scores=((3, 10), (15, 60)))                              # 10.8% (fails) and 62.5%
    assert (await app_client.post(f"{R}/offerings/{offering}/submit")).status_code == 200
    # they are told, at their own address, that marks are waiting
    note = (await db_session.execute(select(Notification).where(Notification.title.like("Marks submitted for approval%")))).scalars().all()
    assert any(n.link == "/dashboard/exam" for n in note) and any(n.link == "/dashboard/admin/result-sheets" for n in note)
    await _as_exam(app_client, exam)
    waiting = (await app_client.get(f"{R}/sheets", params={"status": "SUBMITTED"})).json()
    assert [w["course_code"] for w in waiting] == ["EX1-C1"]
    review = (await app_client.get(f"{R}/sheets/{offering}")).json()
    assert review["stats"]["students"] == 2 and review["status"] == "SUBMITTED"
    assert (await app_client.post(f"{R}/sheets/{offering}/return", json={"reason": "Please recheck the first script"})).json()["status"] == "RETURNED"
    await _as_teacher(app_client, teacher)
    assert (await app_client.post(f"{R}/offerings/{offering}/submit")).status_code == 200
    await _as_exam(app_client, exam)
    assert (await app_client.post(f"{R}/sheets/{offering}/approve")).json()["status"] == "APPROVED"
    assert (await app_client.post(f"{R}/sheets/{offering}/publish")).json()["status"] == "PUBLISHED"
    # after publication they may correct a mark (with a reason), and the failed student's result follows
    fixed = await _mark(app_client, students[0][2], c2, 80, correction_reason="Mark was mis-keyed from the answer sheet")
    assert fixed.status_code == 201
    rows = (await app_client.get(f"{R}/sheets/{offering}")).json()["rows"]
    assert next(r for r in rows if r["enrollment_id"] == students[0][2])["outcome"] == "PASSED"                  # (they read results through the review table, not the class roster)
    assert (await app_client.get(f"{A}/offerings/{offering}/roster")).status_code == 403
    assert (await _mark(app_client, students[0][2], c2, 5)).status_code == 422                                 # a correction still needs its reason
    # the grading scale and the result-type catalogue are theirs to keep
    scale = (await app_client.get(f"{R}/scale")).json()
    assert (await app_client.put(f"{R}/scale", json={"entries": scale})).status_code == 200
    types = (await app_client.get(f"{A}/assessment-types")).json()
    if types:
        assert (await app_client.delete(f"{A}/assessment-types/{types[0]['id']}")).status_code in (204, 422)           # allowed to try; refused only if a subject uses it
    # and they can open any student's result card
    assert (await app_client.get("/api/v1/students")).status_code == 200
    assert (await app_client.get("/api/v1/students/export")).status_code == 403                                  # they may search, never export, the student directory
    card = await app_client.get(f"{R}/students/{students[1][0]}/transcript")
    assert card.status_code == 200 and card.json()["terms"][0]["courses"][0]["letter_grade"] == "C"


async def test_the_exam_department_cannot_reach_anything_else(app_client, admin_user, db_session):
    exam = await _exam_user(app_client, db_session)
    await _as_exam(app_client, exam)
    for path in ("/api/v1/employees", "/api/v1/fees/assignments", "/api/v1/gate/summary", "/api/v1/payroll/runs", "/api/v1/accounts/summary", "/api/v1/purchases",
                 "/api/v1/gate/visitors", "/api/v1/fees/scholarships"):
        assert (await app_client.get(path)).status_code == 403, path
    for call in (app_client.post(f"{A}/enrollments/00000000-0000-0000-0000-000000000000/admin-withdraw"), app_client.post("/api/v1/fees/types", json={"code": "X", "name": "X"}),
                 app_client.post("/api/v1/students", json={})):
        assert (await call).status_code in (403, 422)                                                         # (422 only if the permission check came second; never a success)
    own = await app_client.get("/api/v1/purchases/mine")
    assert own.status_code == 200                                                                              # but like any staff member they may ask for something to be bought
    assert (await app_client.get("/api/v1/payroll/me")).status_code == 200


async def test_a_teacher_still_cannot_approve_results(app_client, admin_user, db_session):
    offering, students, (c1, c2), teacher = await _class(app_client, db_session, code="EX2", n=1, cnic_base=7100)
    await _as_teacher(app_client, teacher)
    await _fill(app_client, students, (c1, c2), scores=((18, 70),))
    await app_client.post(f"{R}/offerings/{offering}/submit")
    assert (await app_client.post(f"{R}/sheets/{offering}/approve")).status_code == 403
    assert (await app_client.get(f"{R}/sheets")).status_code == 403


async def test_the_exam_portal_pages_exist_and_are_for_the_exam_department(app_client):
    for item in EXAM_NAV_ITEMS:
        assert (await app_client.get(item["href"])).status_code == 200, item["href"]
    html = (await app_client.get("/dashboard/exam")).text
    assert 'data-allowed-roles="ADMIN,EXAM_CONTROLLER"' in html and 'data-tab="SCALE"' in html and 'data-tab="CARDS"' in html
    assert 'data-allowed-roles="ADMIN,EXAM_CONTROLLER"' in (await app_client.get("/dashboard/exam/result-types")).text
    tasks = (await app_client.get("/dashboard/exam/tasks")).text
    assert 'data-allowed-roles="ADMIN,EXAM_CONTROLLER"' in tasks and "VIST LMS Exams" in tasks


# ====================================================================== the hostel warden also has somewhere to land
async def test_the_hostel_warden_has_a_portal_of_their_own():
    from app.main import WARDEN_NAV_ITEMS
    assert home_for_roles(["HOSTEL_WARDEN"]) == "/dashboard/warden/home"
    assert home_for_roles(["HOSTEL_WARDEN"], must_change_password=True) == "/change-password"
    assert [i["slug"] for i in WARDEN_NAV_ITEMS] == ["home", "hostel", "announcements", "tasks", "applications", "purchases", "payslips", "calendar", "profile"]
    assert "/dashboard/warden/home" in {i["href"] for i in WARDEN_NAV_ITEMS}                                            # the sign-in landing page is in the menu


async def test_every_hostel_warden_page_opens_and_belongs_to_the_warden(app_client):
    from app.main import WARDEN_NAV_ITEMS
    for item in WARDEN_NAV_ITEMS:
        r = await app_client.get(item["href"])
        assert r.status_code == 200 and 'data-allowed-roles="ADMIN,HOSTEL_WARDEN"' in r.text and "VIST LMS Hostel" in r.text, item["href"]