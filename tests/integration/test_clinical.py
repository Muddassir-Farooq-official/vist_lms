"""Clinical rotations (app/clinical): the office sets up hospitals, wards, groups, supervisors, skills and rotations
(with clash and capacity checks); the rotation's supervisor marks clinical attendance and signs the logbook, and nobody
else's supervisor can; students log entries and see their progress; the placement letter is a PDF."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core.clock import institute_today
from app.core.config import settings
from app.programs.models import Program
from app.students.models import Student
from tests.integration.test_announcements import _first_login
from tests.integration.test_attendance_and_grading import _create_and_activate_student, _login, _login_admin

pytestmark = pytest.mark.asyncio
C = "/api/v1/clinical"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


async def test_rotation_attendance_logbook_and_letter(app_client, admin_user, db_session):
    today = institute_today()
    prog = Program(code="BSNC", name="BS Nursing", duration_years=4, total_semesters=8)
    db_session.add(prog)
    await db_session.commit()
    prog_id = prog.id
    studs = []
    for i in range(3):
        sid, username = await _create_and_activate_student(app_client, cnic=f"35202-77100{i:02d}-1", contact_no=f"0300771{i:04d}")
        studs.append((sid, username))
    for sid, _u in studs:
        (await db_session.get(Student, uuid.UUID(sid))).program_id = prog_id
    await db_session.commit()
    rolls = [(await db_session.get(Student, uuid.UUID(sid))).roll_number for sid, _u in studs]

    await _login_admin(app_client)
    site = (await _ok(await app_client.post(f"{C}/sites", json={"name": "Mayo Hospital", "address": "Lahore", "contact_person": "The MS"})))["id"]
    assert (await app_client.post(f"{C}/sites", json={"name": "mayo hospital"})).status_code == 409
    ward = (await _ok(await app_client.post(f"{C}/sites/{site}/units", json={"name": "Medical Ward", "capacity": 4})))["id"]
    small = (await _ok(await app_client.post(f"{C}/sites/{site}/units", json={"name": "ICU", "capacity": 2})))["id"]
    g1 = (await _ok(await app_client.post(f"{C}/groups", json={"name": "BSN-3 A", "program_id": str(prog_id), "semester": 3, "roll_numbers": rolls[:2]})))["id"]
    g2 = (await _ok(await app_client.post(f"{C}/groups", json={"name": "BSN-3 B", "roll_numbers": rolls[2:]})))["id"]
    assert (await app_client.post(f"{C}/groups", json={"name": "X", "roll_numbers": ["NOPE-1"]})).status_code == 422
    skill = (await _ok(await app_client.post(f"{C}/skills", json={"program_id": str(prog_id), "name": "IV cannulation", "required": 3})))["id"]

    # an external supervisor gets a limited login with a temporary password; a second one supervises another group
    sup = await _ok(await app_client.post(f"{C}/supervisors", json={"name": "Sister Amina", "username": "amina.mayo", "phone": "0300", "site_id": site}))
    assert sup["temporary_password"] and sup["username"] == "amina.mayo"
    other = await _ok(await app_client.post(f"{C}/supervisors", json={"name": "Sister Bushra", "username": "bushra.mayo"}))
    assert (await app_client.post(f"{C}/supervisors", json={"name": "Dup", "username": "AMINA.mayo"})).status_code == 409

    start, end = today - timedelta(days=3), today + timedelta(days=10)
    rot = {"group_id": g1, "unit_id": ward, "supervisor_id": sup["id"], "start_date": str(start), "end_date": str(end), "days": "0,1,2,3,4,5,6",
           "start_time": "08:00", "end_time": "14:00"}
    r1 = (await _ok(await app_client.post(f"{C}/rotations", json=rot)))["id"]
    # the same group elsewhere on overlapping dates: refused
    clash = await app_client.post(f"{C}/rotations", json={**rot, "unit_id": small, "start_date": str(end), "end_date": str(end + timedelta(days=5))})
    assert clash.status_code == 422 and "already at" in clash.text
    # the ward takes 4; group B (1) fits alongside group A (2) ...
    r2 = (await _ok(await app_client.post(f"{C}/rotations", json={**rot, "group_id": g2, "supervisor_id": other["id"]})))["id"]
    # ... but a group bigger than the ICU does not
    big = await app_client.post(f"{C}/rotations", json={**rot, "supervisor_id": None, "group_id": g1, "unit_id": small, "start_date": str(end + timedelta(days=1)),
                                                        "end_date": str(end + timedelta(days=3))})
    assert big.status_code == 200          # 2 students, ICU takes 2: fits
    g3 = (await _ok(await app_client.post(f"{C}/groups", json={"name": "BSN-3 C", "roll_numbers": rolls})))["id"]
    twice = await app_client.post(f"{C}/rotations", json={**rot, "group_id": g3, "supervisor_id": None})       # its students are with A and B then
    assert twice.status_code == 422 and "student(s) of this group are already at" in twice.text
    later = {"start_date": str(end + timedelta(days=20)), "end_date": str(end + timedelta(days=22))}
    full = await app_client.post(f"{C}/rotations", json={**rot, **later, "group_id": g3, "unit_id": small})
    assert full.status_code == 422 and "takes 2" in full.text
    listed = await _ok(await app_client.get(f"{C}/rotations"))
    assert {x["id"] for x in listed} >= {r1, r2}

    letter = await app_client.get(f"{C}/rotations/{r1}/letter.pdf")
    assert letter.status_code == 200 and letter.content[:4] == b"%PDF"

    # a student logs an entry; it waits for the supervisor
    await _login(app_client, studs[0][1], "StudentPass123!")
    me = await _ok(await app_client.get(f"{C}/me"))
    assert r1 in [x["id"] for x in me["rotations"]] and r2 not in [x["id"] for x in me["rotations"]] and me["progress"][0]["required"] == 3
    e1 = (await _ok(await app_client.post(f"{C}/me/logbook", json={"skill_id": skill, "rotation_id": r1, "day": str(today), "times": 2})))["id"]
    e2 = (await _ok(await app_client.post(f"{C}/me/logbook", json={"skill_id": skill, "rotation_id": r1, "day": str(today), "times": 1, "note": "night"})))["id"]
    assert (await app_client.post(f"{C}/me/logbook", json={"skill_id": skill, "rotation_id": r2, "day": str(today)})).status_code == 422   # not my rotation
    assert (await app_client.post(f"{C}/me/logbook", json={"skill_id": skill, "rotation_id": r1, "day": str(today + timedelta(days=1))})).status_code == 422
    assert (await app_client.get(f"{C}/supervise")).status_code == 403        # students cannot supervise
    assert (await app_client.get(f"{C}/setup")).status_code == 403

    # the supervisor signs in (changing the temporary password) and sees only their rotation
    await _first_login(app_client, "amina.mayo", sup["temporary_password"])
    mine = await _ok(await app_client.get(f"{C}/supervise"))
    assert [x["id"] for x in mine] == [r1] and mine[0]["to_sign"] == 2
    assert (await app_client.get(f"{C}/setup")).status_code == 403
    assert (await app_client.get(f"{C}/supervise/{r2}/logbook")).status_code == 403       # someone else's group
    sheet = await _ok(await app_client.get(f"{C}/supervise/{r1}/day", params={"day": str(today)}))
    assert sheet["is_rotation_day"] and len(sheet["students"]) == 2
    marks = [{"student_id": studs[0][0], "status": "PRESENT"}, {"student_id": studs[1][0], "status": "ABSENT"}]
    assert (await _ok(await app_client.post(f"{C}/supervise/{r1}/attendance", json={"day": str(today), "entries": marks})))["marked"] == 2
    assert (await app_client.post(f"{C}/supervise/{r1}/attendance", json={"day": str(today + timedelta(days=1)), "entries": marks})).status_code == 422
    assert (await app_client.post(f"{C}/supervise/{r1}/attendance",
                                  json={"day": str(today), "entries": [{"student_id": studs[2][0], "status": "PRESENT"}]})).status_code == 422
    # a correction overwrites, never duplicates
    await _ok(await app_client.post(f"{C}/supervise/{r1}/attendance", json={"day": str(today), "entries": [{"student_id": studs[1][0], "status": "LEAVE"}]}))
    assert (await _ok(await app_client.post(f"{C}/logbook/{e1}/sign", json={"approve": True})))["status"] == "SIGNED"
    assert (await app_client.post(f"{C}/logbook/{e2}/sign", json={"approve": False})).status_code == 422     # needs a reason
    assert (await _ok(await app_client.post(f"{C}/logbook/{e2}/sign", json={"approve": False, "note": "Not observed"})))["status"] == "REJECTED"
    assert (await app_client.post(f"{C}/logbook/{e1}/sign", json={"approve": False, "note": "changed mind"})).status_code == 422

    # the other supervisor cannot sign group A's entries
    await _first_login(app_client, "bushra.mayo", other["temporary_password"])
    assert (await app_client.post(f"{C}/supervise/{r1}/attendance", json={"day": str(today), "entries": marks})).status_code == 403

    # the student sees attendance and progress
    await _login(app_client, studs[0][1], "StudentPass123!")
    me = await _ok(await app_client.get(f"{C}/me"))
    assert next(x for x in me["rotations"] if x["id"] == r1)["attendance"]["percentage"] == 100.0
    p = me["progress"][0]
    assert p["signed"] == 2 and p["waiting"] == 0 and not p["complete"]
    assert {e["status"] for e in me["entries"]} == {"SIGNED", "REJECTED"}
    rej = next(e for e in me["entries"] if e["status"] == "REJECTED")
    assert rej["reject_note"] == "Not observed"
    assert (await app_client.delete(f"{C}/me/logbook/{e1}")).status_code == 422       # signed entries stay

    # the office: a rotation with records cannot be removed; group progress; switching the hospital login off
    await _login_admin(app_client)
    assert (await app_client.delete(f"{C}/rotations/{r1}")).status_code == 422
    rep = await _ok(await app_client.get(f"{C}/groups/{g1}/report"))
    by = {s["id"]: s for s in rep["students"]}
    assert by[studs[0][0]]["attendance"] == 100.0 and by[studs[1][0]]["attendance"] is None
    await _ok(await app_client.post(f"{C}/supervisors/{other['id']}/active", json={"is_active": False}))
    bad = await app_client.post("/api/v1/auth/login", json={"username": "bushra.mayo", "password": "Chosen#Pass-2026x"})
    assert bad.status_code != 200


async def test_pages_render(app_client, admin_user):
    await _login_admin(app_client)
    for path in ("/dashboard/admin/clinical", "/dashboard/clinical", "/dashboard/student/clinical", "/dashboard/clinical/help"):
        r = await app_client.get(path)
        assert r.status_code == 200, path
    assert "My rotations" in (await app_client.get("/dashboard/clinical/help")).text
