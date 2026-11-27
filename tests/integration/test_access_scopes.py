"""Department and program limits (1.73.0): someone limited to some programs / departments only sees and works on what
is inside them (timetable batches, attendance classes, students, result sheets, requests to decide). A HOD without
limits keeps their own department. Administrators are never limited."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from tests.integration.test_access_control import A, _person
from tests.integration.test_fees import _login, _login_admin
from tests.integration.test_smart_timetable import _q as _q  # noqa: F401  (rate limits off)

pytestmark = pytest.mark.asyncio
S = "/api/v1/academics/timetable/smart"


async def _two_programs(client):
    await _login_admin(client)
    progs = {}
    for code in ("SCA", "SCB"):
        r = await client.post("/api/v1/programs", json={"code": code, "name": f"{code} Program", "duration_years": 4, "total_semesters": 8})
        progs[code] = r.json()["id"]
        t = await client.post("/api/v1/academics/academic-terms", json={"label": f"{code} 1st Fall 2026", "start_date": "2026-09-01",
                                                                        "end_date": "2027-01-15", "program_id": progs[code], "semester": 1})
        assert t.status_code in (200, 201), t.text
    return progs


async def _student(client, n, program_id):
    r = await client.post("/api/v1/students", json={"first_name": f"Sc{n}", "last_name": "Ope", "date_of_birth": "2005-01-01", "gender": "Male",
                                                    "cnic": f"35202-84000{n:02d}-1", "contact_no": "03001234567", "marital_status": "Single", "religion": "Islam",
                                                    "nationality": "Pakistani", "guardian_name": "G", "guardian_cnic": f"35202-85000{n:02d}-1",
                                                    "relation_with_guardian": "Father", "emergency_contact_no": "03001234568", "present_address": "X",
                                                    "province": "Punjab", "district": "Lahore", "city": "Lahore", "program_id": program_id, "current_semester": 1})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_a_person_limited_to_one_program_sees_only_its_batches_and_students(app_client, admin_user, db_session):
    progs = await _two_programs(app_client)
    sa, sb = await _student(app_client, 1, progs["SCA"]), await _student(app_client, 2, progs["SCB"])
    t = await _person(app_client, db_session, "SCT", "EXAM_CONTROLLER", "35202-8400099-9")      # has the timetable and looks students up
    r = await app_client.put(f"{A}/people/{t['user_id']}/scope", json={"programs": [progs["SCA"]], "departments": []})
    assert r.status_code == 200 and r.json()["changed"] is True, r.text
    now = (await app_client.get(f"{A}/people/{t['user_id']}/scope")).json()
    assert now["programs"] == [progs["SCA"]] and now["now"] == "SCA"

    await _login(app_client, t["username"], t["pw"])
    terms = (await app_client.get("/api/v1/academics/terms")).json()
    assert "SCA 1st Fall 2026" in terms and "SCB 1st Fall 2026" not in terms
    assert (await app_client.get(f"{S}/readiness", params={"terms": "SCA 1st Fall 2026"})).status_code == 200
    out = await app_client.get(f"{S}/readiness", params={"terms": "SCB 1st Fall 2026"})
    assert out.status_code == 403 and "outside the programs" in out.text
    found = (await app_client.get("/api/v1/students", params={"page_size": 50})).json()["items"]
    assert {s["id"] for s in found} == {sa}
    assert (await app_client.get(f"/api/v1/students/{sb}")).status_code == 403
    assert (await app_client.get(f"/api/v1/students/{sa}")).status_code == 200

    # undo takes the limit away again
    await _login_admin(app_client)
    change = next(h for h in (await app_client.get(f"{A}/history")).json() if h["kind"] == "PERSON_SCOPE")
    assert (await app_client.post(f"{A}/history/{change['id']}/undo")).json()["done"] is True
    await _login(app_client, t["username"], t["pw"])
    assert (await app_client.get(f"{S}/readiness", params={"terms": "SCB 1st Fall 2026"})).status_code == 200


async def test_administrators_are_never_limited_and_nobody_limits_themselves(app_client, admin_user, db_session):
    progs = await _two_programs(app_client)
    bad = await app_client.put(f"{A}/people/{admin_user.id}/scope", json={"programs": [progs["SCA"]], "departments": []})
    assert bad.status_code == 422
    t = await _person(app_client, db_session, "SCU", "TEACHER", "35202-8400098-8")
    await app_client.put(f"/api/v1/employees/{t['emp']}/access-roles", json={"extra_roles": ["ADMIN"]})
    await _login(app_client, t["username"], t["pw"])
    own = await app_client.put(f"{A}/people/{t['user_id']}/scope", json={"programs": [progs["SCA"]], "departments": []})
    assert own.status_code == 422


async def test_a_department_limit_narrows_leave_to_decide(app_client, admin_user, db_session):
    from datetime import timedelta

    from app.core.clock import institute_today
    from app.employees.models import Employee

    p = await _person(app_client, db_session, "SCP", "PRINCIPAL", "35202-8400097-7")
    nurse = await _person(app_client, db_session, "SCN", "TEACHER", "35202-8400096-6")
    other = await _person(app_client, db_session, "SCO", "TEACHER", "35202-8400095-5")
    for who, dept in ((nurse, "Nursing"), (other, "Allied Health")):
        e = (await db_session.execute(select(Employee).where(Employee.user_id == uuid.UUID(who["user_id"])))).scalar_one()
        e.department = dept
    await db_session.commit()
    await app_client.put(f"{A}/approval-rules/STAFF_LEAVE", json={"rules": [{"mode": "APPROVE", "approver_type": "ROLE", "approver_value": "PRINCIPAL"}]})
    await app_client.put(f"{A}/people/{p['user_id']}/scope", json={"programs": [], "departments": ["nursing"]})
    day = institute_today() + timedelta(days=21)
    ids = {}
    for who in (nurse, other):
        await _login(app_client, who["username"], who["pw"])
        r = await app_client.post("/api/v1/staff-attendance/leave", data={"kind": "UNPAID", "first_day": str(day), "last_day": str(day), "reason": "Family matter"})
        assert r.status_code == 201, r.text
        ids[who["username"]] = r.json()["id"]
    await _login(app_client, p["username"], p["pw"])
    mine = [x["id"] for x in (await app_client.get("/api/v1/decisions")).json()]
    assert mine == [ids[nurse["username"]]]
    out = await app_client.post(f"/api/v1/decisions/STAFF_LEAVE/{ids[other['username']]}", json={"approve": True})
    assert out.status_code == 403 and "outside" in out.text


async def test_a_hod_keeps_their_own_department_without_limits(app_client, admin_user, db_session):
    from app.access.scope import get_scope
    from app.auth.schemas import CurrentUser
    from app.employees.models import Employee

    hod = await _person(app_client, db_session, "SCH", "HOD", "35202-8400094-4")
    e = (await db_session.execute(select(Employee).where(Employee.user_id == uuid.UUID(hod["user_id"])))).scalar_one()
    e.department = "  Nursing "
    await db_session.commit()
    cu = CurrentUser(id=uuid.UUID(hod["user_id"]), username=hod["username"], roles=["HOD"], permissions=[], must_change_password=False)
    sc = await get_scope(db_session, cu)
    assert sc is not None and sc.source == "hod" and sc.departments == frozenset({"nursing"})
    admin = CurrentUser(id=admin_user.id, username="a", roles=["ADMIN", "HOD"], permissions=[], must_change_password=False)
    assert await get_scope(db_session, admin) is None


async def test_refusals_say_which_right_is_missing_and_buttons_say_what_they_need(app_client, admin_user, db_session):
    t = await _person(app_client, db_session, "SCV", "TEACHER", "35202-8400093-3")
    await _login(app_client, t["username"], t["pw"])
    r = await app_client.get("/api/v1/academics/timetable/smart/latest")
    assert r.status_code == 403
    msg = r.json()["error"]["message"]
    assert "You do not have the right to: Edit the timetable" in msg and "Missing required permission" in msg
    await _login_admin(app_client)
    page = (await app_client.get("/dashboard/admin/students")).text
    assert 'id="open-add-student" data-need="student.create"' in page
    assert 'data-tab="performance" role="tab" data-need="enrollment.manage"' in (await app_client.get("/dashboard/admin/courses")).text
