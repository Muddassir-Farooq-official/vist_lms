"""Graduation and alumni (app/alumni): a graduation list checks every student; only ticked students graduate (one not
clear needs a recorded reason); a graduate keeps their login as an alumnus (student role replaced); degrees and
transcripts are tracked to hand-over; alumni keep their details, choose to be in the directory, and answer surveys."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.auth.models import Role, User, UserRole
from app.core.config import settings
from app.notifications.models import Notification
from app.programs.models import Program
from app.students.models import Student, StudentStatus
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _create_and_activate_student, _login, _login_admin

pytestmark = pytest.mark.asyncio
A = "/api/v1/alumni"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


async def test_graduation_alumni_documents_directory_and_surveys(app_client, admin_user, db_session):
    prog = Program(code="ALM", name="BS Alumni Studies", duration_years=4, total_semesters=8)
    db_session.add(prog)
    await db_session.commit()
    prog_id = prog.id
    (a, ua), (b, ub) = [await _create_and_activate_student(app_client, cnic=f"35202-88100{i:02d}-1", contact_no=f"0300881{i:04d}") for i in range(2)]
    for sid in (a, b):
        (await db_session.get(Student, uuid.UUID(sid))).program_id = prog_id
    await db_session.commit()
    roll_a = (await db_session.get(Student, uuid.UUID(a))).roll_number

    await _login_admin(app_client)
    gl = await _ok(await app_client.post(f"{A}/graduation", json={"program_id": str(prog_id), "title": "Convocation 2026", "graduation_date": "2026-12-01"}), 201)
    assert len(gl["entries"]) == 2 and not any(e["included"] for e in gl["entries"])            # no course list -> nobody is clear
    assert all(e["problems"] for e in gl["entries"])
    assert (await app_client.post(f"{A}/graduation", json={"program_id": str(prog_id), "title": "Again", "graduation_date": "2026-12-01"})).status_code == 422
    ea = next(e for e in gl["entries"] if e["student_id"] == a)
    eb = next(e for e in gl["entries"] if e["student_id"] == b)
    assert (await app_client.post(f"{A}/graduation/{gl['id']}/entries/{eb['id']}", json={"included": True})).status_code == 422     # needs a reason
    await _ok(await app_client.post(f"{A}/graduation/{gl['id']}/entries/{ea['id']}", json={"included": True, "note": "Curriculum audited on paper by the principal"}))
    pdf = await app_client.get(f"{A}/graduation/{gl['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    done = await _ok(await app_client.post(f"{A}/graduation/{gl['id']}/confirm"))
    assert done["graduated"] == 1 and done["skipped"] == 0 and done["status"] == "CONFIRMED"
    assert (await app_client.post(f"{A}/graduation/{gl['id']}/confirm")).status_code == 422

    db_session.expire_all()
    sa = await db_session.get(Student, uuid.UUID(a))
    assert sa.status == StudentStatus.GRADUATED and (await db_session.get(Student, uuid.UUID(b))).status == StudentStatus.ACTIVE
    user = await db_session.get(User, sa.user_id)
    codes = set((await db_session.execute(select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user.id))).scalars())
    assert user.is_active and codes == {"ALUMNUS"}
    user_id = user.id

    # the exam office tracks the degree
    exam, first = await _staff(app_client, db_session, "EXAL", "EXAM_CONTROLLER", "35202-8810101-1")
    await _first_login(app_client, exam, first)
    assert (await app_client.post(f"{A}/documents", json={"student_id": b, "kind": "DEGREE"})).status_code == 422       # not graduated
    deg = await _ok(await app_client.post(f"{A}/documents", json={"roll_number": roll_a, "kind": "DEGREE"}), 201)
    assert (await app_client.post(f"{A}/documents", json={"student_id": a, "kind": "DEGREE"})).status_code == 422       # already on its way
    assert (await app_client.post(f"{A}/documents/{deg['id']}", json={"status": "READY"})).status_code == 422         # must be prepared first
    await _ok(await app_client.post(f"{A}/documents/{deg['id']}", json={"status": "PREPARING"}))
    assert (await app_client.post(f"{A}/documents/{deg['id']}", json={"status": "READY"})).status_code == 422         # needs its serial number
    await _ok(await app_client.post(f"{A}/documents/{deg['id']}", json={"status": "READY", "serial_no": "DEG-2026-0001"}))
    assert (await app_client.get(f"{A}/graduation")).status_code == 403                                               # lists are the administration's

    # the graduate signs in with their old login, as an alumnus
    await _login(app_client, ua, "StudentPass123!")
    me = await _ok(await app_client.get(f"{A}/me"))
    assert me["documents"][0]["status"] == "READY" and me["documents"][0]["serial_no"] == "DEG-2026-0001"
    db_session.expire_all()
    assert (await db_session.execute(select(Notification).where(Notification.user_id == user_id, Notification.title.like("Your degree is ready%")))).first()
    assert (await app_client.get(f"{A}/directory")).json() == []                                                      # not listed until they choose
    await _ok(await app_client.put(f"{A}/me", json={"employer": "Mayo Hospital", "job_title": "Charge Nurse", "city": "Lahore", "email": "a@example.com",
                                                    "in_directory": True, "share_contact": False}))
    listed = await _ok(await app_client.get(f"{A}/directory"))
    assert listed[0]["employer"] == "Mayo Hospital" and listed[0]["email"] is None                                    # contact kept private
    t = await _ok(await app_client.post(f"{A}/me/transcript", json={"copies": 2}), 201)
    assert t["status"] == "REQUESTED"
    assert (await app_client.get("/api/v1/qbank/courses")).status_code == 403
    assert (await app_client.get(f"{A}/documents")).status_code == 403

    # a current student is not an alumnus
    await _login(app_client, ub, "StudentPass123!")
    assert (await app_client.get(f"{A}/me")).status_code == 403

    # surveys
    await _login_admin(app_client)
    sv = await _ok(await app_client.post(f"{A}/surveys", json={"title": "Graduate survey", "questions": [
        {"text": "How ready were you for work?", "kind": "RATING"}, {"text": "Now?", "kind": "CHOICE", "choices": ["Employed", "Studying"]},
        {"text": "Teach better?", "kind": "TEXT"}]}), 201)
    await _ok(await app_client.put(f"{A}/surveys/{sv['id']}", json={"title": "Graduate survey", "is_open": True}))
    everyone = await _ok(await app_client.get(f"{A}/directory/all"))
    assert everyone[0]["email"] == "a@example.com"
    csv = await app_client.get(f"{A}/directory/all.csv")
    assert csv.status_code == 200 and "Mayo Hospital" in csv.text

    await _login(app_client, ua, "StudentPass123!")
    open_ = await _ok(await app_client.get(f"{A}/me/surveys"))
    assert open_[0]["id"] == sv["id"] and not open_[0]["answered"]
    assert (await app_client.post(f"{A}/me/surveys/{sv['id']}", json={"answers": {"q1": 9}})).status_code == 422
    await _ok(await app_client.post(f"{A}/me/surveys/{sv['id']}", json={"answers": {"q1": 4, "q2": "Employed", "q3": "More ward time"}}))
    assert (await app_client.post(f"{A}/me/surveys/{sv['id']}", json={"answers": {"q1": 5}})).status_code == 422

    await _login_admin(app_client)
    res = await _ok(await app_client.get(f"{A}/surveys/{sv['id']}/results"))
    assert res["responses"] == 1 and res["questions"][0]["average"] == 4.0 and res["questions"][1]["counts"]["Employed"] == 1
    assert (await app_client.put(f"{A}/surveys/{sv['id']}", json={"title": "x x x", "questions": [{"text": "New q", "kind": "TEXT"}]})).status_code == 422
    docs = await _ok(await app_client.get(f"{A}/documents"))
    assert {d["kind"] for d in docs} == {"DEGREE", "TRANSCRIPT"}


async def test_pages_render(app_client, admin_user):
    await _login_admin(app_client)
    for path in ("/dashboard/admin/alumni", "/dashboard/exam/degrees", "/dashboard/alumni", "/dashboard/alumni/help"):
        assert (await app_client.get(path)).status_code == 200, path
