"""The internship year (app/internship): the administration starts internships and places interns; the intern logs
duties (only on placement days, not on approved leave, not twice); the placement's supervisor verifies and evaluates
(another supervisor cannot); completion needs the hours and an evaluation (or a recorded reason); a graduation list
does not clear a student whose internship is ongoing."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core.clock import institute_today
from app.core.config import settings
from app.students.models import Student
from tests.integration.test_announcements import _first_login
from tests.integration.test_attendance_and_grading import _create_and_activate_student, _login, _login_admin

pytestmark = pytest.mark.asyncio
I, C = "/api/v1/internship", "/api/v1/clinical"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


async def test_internship_flow(app_client, admin_user, db_session):
    sid, susr = await _create_and_activate_student(app_client, cnic="35202-9410001-1", contact_no="03009410001")
    other_sid, other_usr = await _create_and_activate_student(app_client, cnic="35202-9410002-1", contact_no="03009410002")
    roll = (await db_session.get(Student, uuid.UUID(sid))).roll_number
    today = institute_today()

    await _login_admin(app_client)
    site = (await _ok(await app_client.post(f"{C}/sites", json={"name": "Services Hospital"})))["id"]
    unit = (await _ok(await app_client.post(f"{C}/sites/{site}/units", json={"name": "Surgical Ward", "capacity": 10})))["id"]
    sup = await _ok(await app_client.post(f"{C}/supervisors", json={"name": "Sister Rubina", "username": "rubina.services"}))
    other_sup = await _ok(await app_client.post(f"{C}/supervisors", json={"name": "Sister Nadia", "username": "nadia.services"}))

    assert (await app_client.post(f"{I}/start", json={"roll_numbers": ["NOPE"], "start_date": str(today), "end_date": str(today + timedelta(days=300)),
                                                       "required_hours": 12})).status_code == 422
    st = await _ok(await app_client.post(f"{I}/start", json={"roll_numbers": [roll], "start_date": str(today - timedelta(days=10)),
                                                             "end_date": str(today + timedelta(days=300)), "required_hours": 12}))
    assert st["started"] == 1
    again = await _ok(await app_client.post(f"{I}/start", json={"roll_numbers": [roll], "start_date": str(today), "end_date": str(today + timedelta(days=30)),
                                                                "required_hours": 12}))
    assert again["started"] == 0 and "already" in again["skipped"][0]
    iid = (await _ok(await app_client.get(f"{I}/interns")))[0]["id"]
    assert (await app_client.post(f"{I}/interns/{iid}/placements", json={"unit_id": unit, "start_date": str(today - timedelta(days=30)),
                                                                          "end_date": str(today)})).status_code == 422     # before the internship
    pl = (await _ok(await app_client.post(f"{I}/interns/{iid}/placements", json={"unit_id": unit, "supervisor_id": sup["id"],
                                                                                  "start_date": str(today - timedelta(days=7)), "end_date": str(today + timedelta(days=30))}), 201))["id"]

    # the intern logs duties and asks for leave
    await _login(app_client, susr, "StudentPass123!")
    me = await _ok(await app_client.get(f"{I}/me"))
    assert me["required_hours"] == 12 and me["placements"][0]["supervisor"] == "Sister Rubina"
    d1 = await _ok(await app_client.post(f"{I}/me/duties", json={"day": str(today - timedelta(days=2)), "shift": "MORNING", "hours": 8}), 201)
    d2 = await _ok(await app_client.post(f"{I}/me/duties", json={"day": str(today - timedelta(days=1)), "shift": "NIGHT", "hours": 6}), 201)
    assert (await app_client.post(f"{I}/me/duties", json={"day": str(today - timedelta(days=1)), "shift": "NIGHT", "hours": 6})).status_code == 422   # twice
    assert (await app_client.post(f"{I}/me/duties", json={"day": str(today - timedelta(days=9)), "shift": "MORNING", "hours": 6})).status_code == 422  # not placed
    assert (await app_client.post(f"{I}/me/duties", json={"day": str(today + timedelta(days=1)), "shift": "MORNING", "hours": 6})).status_code == 422  # ahead
    lv = await _ok(await app_client.post(f"{I}/me/leaves", json={"from_date": str(today), "to_date": str(today), "reason": "Family wedding"}), 201)
    await _login(app_client, other_usr, "StudentPass123!")
    assert (await _ok(await app_client.get(f"{I}/me"))) is None
    assert (await app_client.post(f"{I}/me/duties", json={"day": str(today), "shift": "MORNING", "hours": 6})).status_code == 422

    # leave approved: no duty on that day
    await _login_admin(app_client)
    await _ok(await app_client.post(f"{I}/leaves/{lv['id']}", json={"approve": True}))
    await _login(app_client, susr, "StudentPass123!")
    onleave = await app_client.post(f"{I}/me/duties", json={"day": str(today), "shift": "MORNING", "hours": 6})
    assert onleave.status_code == 422 and "leave" in onleave.text

    # the other supervisor cannot verify; the placement's supervisor verifies and evaluates
    await _first_login(app_client, "nadia.services", other_sup["temporary_password"])
    assert await _ok(await app_client.get(f"{I}/supervise")) == []
    assert (await app_client.post(f"{I}/duties/{d1['id']}", json={"approve": True})).status_code == 403
    await _first_login(app_client, "rubina.services", sup["temporary_password"])
    mine = await _ok(await app_client.get(f"{I}/supervise"))
    assert len(mine) == 1 and len(mine[0]["logs"]) == 2
    await _ok(await app_client.post(f"{I}/duties/{d1['id']}", json={"approve": True}))
    assert (await app_client.post(f"{I}/duties/{d2['id']}", json={"approve": False})).status_code == 422          # needs a reason
    await _ok(await app_client.post(f"{I}/duties/{d2['id']}", json={"approve": False, "note": "You left at 3 AM"}))
    assert (await app_client.post(f"{I}/placements/{pl}/evaluate", json={"scores": {"clinical_skills": 6}})).status_code == 422
    ev = await _ok(await app_client.post(f"{I}/placements/{pl}/evaluate", json={"scores": {"clinical_skills": 4, "professionalism": 5, "communication": 4,
                                                                                         "punctuality": 3, "patient_care": 4}, "comment": "Good"}), 201)
    assert ev["average"] == 4.0

    # completion: 8 of 12 hours -> needs a reason; the graduation check sees the ongoing internship
    await _login_admin(app_client)
    from app.internship.service import graduation_problem

    assert await graduation_problem(db_session, uuid.UUID(sid)) == "Internship not complete."
    assert await graduation_problem(db_session, uuid.UUID(other_sid)) is None
    short = await app_client.post(f"{I}/interns/{iid}/complete", json={})
    assert short.status_code == 422 and "8 of 12 hours" in short.text
    await _ok(await app_client.post(f"{I}/interns/{iid}/complete", json={"note": "Remaining hours done at a partner hospital (letter on file)"}))
    db_session.expire_all()
    assert await graduation_problem(db_session, uuid.UUID(sid)) is None
    det = await _ok(await app_client.get(f"{I}/interns/{iid}"))
    assert det["status"] == "COMPLETED" and det["verified_hours"] == 8.0 and det["leave_days"] == 1
    assert (await app_client.post(f"{I}/interns/{iid}/complete", json={})).status_code == 422


async def test_pages_render(app_client, admin_user):
    await _login_admin(app_client)
    for path in ("/dashboard/admin/clinical", "/dashboard/clinical", "/dashboard/student/clinical"):
        r = await app_client.get(path)
        assert r.status_code == 200, path
    assert "internship-admin.js" in (await app_client.get("/dashboard/admin/clinical")).text
