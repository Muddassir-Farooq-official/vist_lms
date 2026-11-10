"""Warnings to students and staff: admin issues, the 3rd within 12 months is final, admin or principal decides the action."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.discipline.models import DisciplinaryWarning
from app.notifications.models import Notification
from app.sms.models import SmsMessage
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _login, _login_admin, _setup_offering_with_enrolled_students

pytestmark = pytest.mark.asyncio
PW = "StudentPass123!"
API = "/api/v1/discipline"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(settings, "sms_to_guardian", 1)


async def _types(client, kind):
    r = await client.get(f"{API}/violation-types", params={"kind": kind})
    assert r.status_code == 200, r.text
    return {t["name"]: t["id"] for t in r.json()}


async def _warn(client, kind, pid, type_id, note="Was found doing this again in the corridor.", status=201):
    r = await client.post(f"{API}/warnings", json={"kind": kind, "person_id": pid, "violation_type_id": type_id, "note": note})
    assert r.status_code == status, r.text
    return r.json()


async def test_three_warnings_make_a_final_one_and_the_principal_suspends_the_student(app_client, admin_user, db_session):
    from app.students.models import Student, StudentGuardian

    _off, studs = await _setup_offering_with_enrolled_students(app_client, code="WR1", n_students=1, cnic_base=8400)
    sid, username, _ = studs[0]
    await db_session.execute(update(StudentGuardian).where(StudentGuardian.student_id == uuid.UUID(sid)).values(contact_no="0300-1234567"))
    await db_session.commit()
    await _login_admin(app_client)
    types = await _types(app_client, "STUDENT")
    assert "Cheating or unfair means in an exam" in types and "Negligence of duty" not in types        # the student list
    found = (await app_client.get(f"{API}/people", params={"kind": "STUDENT", "q": username})).json()
    assert found[0]["id"] == sid and found[0]["counting"] == 0

    one = await _warn(app_client, "STUDENT", sid, types["Late to class again and again"])
    assert one == {"id": one["id"], "number": 1, "is_final": False}
    assert (await _warn(app_client, "STUDENT", sid, types["Using a mobile phone in class"]))["number"] == 2
    three = await _warn(app_client, "STUDENT", sid, types["Fighting or bullying"])
    assert three["number"] == 3 and three["is_final"] is True
    await _warn(app_client, "STUDENT", sid, types["Other"], status=422)                                  # decide first

    notes = (await db_session.execute(select(Notification).where(Notification.title.like("%Warning%") | Notification.title.like("Final warning%")))).scalars().all()
    assert {n.link for n in notes if n.title.startswith(("Warning", "Final warning ("))} == {"/dashboard/my-warnings"}
    assert any(n.title.startswith("Final warning: decide") for n in notes)
    sms = (await db_session.execute(select(SmsMessage).where(SmsMessage.event == "WARNING"))).scalars().all()
    assert sms == []                                                                                     # parents are not messaged unless switched on

    # the student sees them, downloads the letter and replies
    await _login(app_client, username, PW)
    mine = (await app_client.get(f"{API}/me/warnings")).json()
    assert mine["counting"] == 3 and mine["limit"] == 3 and [w["number"] for w in mine["warnings"]] == [3, 2, 1]
    assert mine["warnings"][0]["action"] == "PENDING" and mine["warnings"][0]["can_reply"]
    pdf = await app_client.get(f"{API}/me/warnings/{three['id']}/letter")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert mine["warnings"][0]["acknowledged_at"] is None
    ack = await app_client.post(f"{API}/me/warnings/{one['id']}/acknowledge")
    assert ack.status_code == 200 and ack.json()["acknowledged_at"]
    assert (await app_client.post(f"{API}/me/warnings/{one['id']}/acknowledge")).json()["acknowledged_at"] == ack.json()["acknowledged_at"]   # once
    assert (await app_client.post(f"{API}/me/warnings/{three['id']}/reply", json={"text": "I was defending myself, please check the camera."})).status_code == 200
    again = await app_client.post(f"{API}/me/warnings/{three['id']}/reply", json={"text": "And one more thing I forgot to say."})
    assert again.status_code in (400, 409, 422) and "already replied" in again.text                    # one reply only
    after = {w["id"]: w for w in (await app_client.get(f"{API}/me/warnings")).json()["warnings"]}
    assert after[three["id"]]["acknowledged_at"] and after[three["id"]]["can_reply"] is False           # replying counts as read
    assert (await app_client.get(f"{API}/warnings")).status_code == 403                                 # students do not see the office list
    assert (await app_client.post(f"{API}/warnings", json={"kind": "STUDENT", "person_id": sid, "violation_type_id": types["Other"], "note": "x" * 20})).status_code == 403
    page = await app_client.get("/dashboard/student/my-warnings", headers={"accept": "text/html"})
    assert page.status_code == 200 and "my-warnings.js" in page.text

    # the principal decides: suspension, applied to the record
    await _login_admin(app_client)
    p_user, p_first = await _staff(app_client, db_session, "PRINW", "TEACHER", "35202-4810001-1")
    emp = (await app_client.get("/api/v1/employees", params={"q": p_user})).json()["items"][0]["id"]
    assert (await app_client.put(f"/api/v1/employees/{emp}/access-roles", json={"extra_roles": ["PRINCIPAL"]})).status_code == 200
    await _first_login(app_client, p_user, p_first)
    pending = (await app_client.get(f"{API}/warnings", params={"view": "pending"})).json()
    assert pending["pending"] == 1 and pending["replies"] == 1 and pending["warnings"][0]["response"].startswith("I was defending")
    assert (await app_client.post(f"{API}/warnings", json={"kind": "STUDENT", "person_id": sid, "violation_type_id": types["Other"], "note": "x" * 20})).status_code == 403
    assert (await app_client.post(f"{API}/warnings/{three['id']}/decide", json={"action": "TERMINATE", "note": "Not a student action at all."})).status_code == 422
    d = await app_client.post(f"{API}/warnings/{three['id']}/decide", json={"action": "SUSPEND", "note": "Suspended for two weeks after the inquiry."})
    assert d.status_code == 200 and d.json()["action"] == "SUSPEND"
    s = await db_session.get(Student, uuid.UUID(sid))
    await db_session.refresh(s)
    assert s.status.value == "SUSPENDED"
    assert (await app_client.post(f"{API}/warnings/{three['id']}/withdraw", json={"note": "Changed our mind"})).status_code == 422   # action taken
    letter = await app_client.get(f"{API}/warnings/{three['id']}/letter")
    assert letter.status_code == 200 and letter.content[:4] == b"%PDF"
    for page in ("/dashboard/principal/warnings", "/dashboard/admin/warnings"):
        assert (await app_client.get(page, headers={"accept": "text/html"})).status_code == 200

    # the suspension is ended from the same screen: the student is active (and can sign in) again
    assert (await app_client.post(f"{API}/warnings/{three['id']}/lift", json={"note": "x"})).status_code == 422
    lifted = await app_client.post(f"{API}/warnings/{three['id']}/lift", json={"note": "Inquiry over, two weeks served."})
    assert lifted.status_code == 200
    await db_session.refresh(s)
    assert s.status.value == "ACTIVE"
    assert (await app_client.post(f"{API}/warnings/{three['id']}/lift", json={"note": "Inquiry over, two weeks served."})).status_code in (400, 409, 422)
    assert (await app_client.post("/api/v1/auth/login", json={"username": username, "password": PW})).status_code == 200


async def test_staff_warnings_withdrawal_and_the_12_month_window(app_client, admin_user, db_session):
    from app.employees.models import Employee

    await _login_admin(app_client)
    username, first = await _staff(app_client, db_session, "WRSTF", "STAFF", "35202-4820001-1")
    emp = (await db_session.execute(select(Employee).where(Employee.employee_code.isnot(None)).order_by(Employee.created_at.desc()))).scalars().first()
    types = await _types(app_client, "EMPLOYEE")
    assert "Negligence of duty" in types and "Fighting or bullying" not in types
    a = await _warn(app_client, "EMPLOYEE", str(emp.id), types["Late arrival or leaving early"])
    b = await _warn(app_client, "EMPLOYEE", str(emp.id), types["Negligence of duty"])
    assert (await app_client.post(f"{API}/warnings/{b['id']}/withdraw", json={"note": "Duty roster was wrong"})).json()["status"] == "WITHDRAWN"
    # a warning older than 12 months no longer counts
    await db_session.execute(update(DisciplinaryWarning).where(DisciplinaryWarning.id == uuid.UUID(a["id"]))
                             .values(created_at=datetime.now(timezone.utc) - timedelta(days=366)))
    await db_session.commit()
    c = await _warn(app_client, "EMPLOYEE", str(emp.id), types["Absent without approved leave"])
    assert c["number"] == 1 and c["is_final"] is False

    # a violation added by the admin, and one turned off
    add = await app_client.post(f"{API}/violation-types", json={"applies_to": "EMPLOYEE", "name": "Using the phone at the gate"})
    assert add.status_code == 201
    assert (await app_client.post(f"{API}/violation-types", json={"applies_to": "EMPLOYEE", "name": "using the phone at the gate"})).status_code == 422
    off = types["Breach of confidentiality"]
    assert (await app_client.patch(f"{API}/violation-types/{off}", json={"is_active": False})).json()["is_active"] is False
    assert "Breach of confidentiality" not in await _types(app_client, "EMPLOYEE")
    await _warn(app_client, "EMPLOYEE", str(emp.id), off, status=422)

    second = await _warn(app_client, "EMPLOYEE", str(emp.id), add.json()["id"])

    # the staff member's own page, the redirect that notifications use, and a reply
    await _first_login(app_client, username, first)
    mine = (await app_client.get(f"{API}/me/warnings")).json()
    assert {w["status"] for w in mine["warnings"]} == {"ACTIVE", "WITHDRAWN"} and mine["counting"] == 2
    r = await app_client.get("/dashboard/my-warnings", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/dashboard/staff/my-warnings"
    assert (await app_client.get("/dashboard/staff/my-warnings", headers={"accept": "text/html"})).status_code == 200
    assert (await app_client.post(f"{API}/me/warnings/{second['id']}/reply", json={"text": "I was told to keep the phone for emergencies."})).status_code == 200

    await _login_admin(app_client)
    final = await _warn(app_client, "EMPLOYEE", str(emp.id), types["Harassment"])
    assert final["number"] == 3 and final["is_final"]
    t = await app_client.post(f"{API}/warnings/{final['id']}/decide", json={"action": "TERMINATE", "note": "Terminated after the inquiry committee report."})
    assert t.status_code == 200
    await db_session.refresh(emp)
    assert emp.status.value == "TERMINATED"
    await _warn(app_client, "EMPLOYEE", str(emp.id), types["Other"], status=422)                          # no longer working here
    assert (await app_client.post(f"{API}/warnings/{final['id']}/decide", json={"action": "NONE", "note": "Deciding a second time."})).status_code == 422


async def test_nobody_reads_or_answers_someone_elses_warning(app_client, admin_user, db_session):
    _off, studs = await _setup_offering_with_enrolled_students(app_client, code="WR3", n_students=2, cnic_base=8410)
    (sid, _u1, _), (_sid2, u2, _) = studs
    await _login_admin(app_client)
    w = await _warn(app_client, "STUDENT", sid, (await _types(app_client, "STUDENT"))["Other"])
    await _login(app_client, u2, PW)
    assert (await app_client.get(f"{API}/me/warnings")).json()["warnings"] == []
    assert (await app_client.get(f"{API}/me/warnings/{w['id']}/letter")).status_code == 404
    assert (await app_client.post(f"{API}/me/warnings/{w['id']}/reply", json={"text": "This is not even mine to answer."})).status_code == 404


async def test_staff_final_chance_then_fine_then_suspension_that_blocks_sign_in(app_client, admin_user, db_session):
    from app.auth.models import User
    from app.employees.models import Employee
    from app.fines.models import Fine

    await _login_admin(app_client)
    username, first = await _staff(app_client, db_session, "WRFIN", "STAFF", "35202-4830001-1")
    emp = (await db_session.execute(select(Employee).where(Employee.employee_code.isnot(None)).order_by(Employee.created_at.desc()))).scalars().first()
    types = await _types(app_client, "EMPLOYEE")
    for _ in range(2):
        await _warn(app_client, "EMPLOYEE", str(emp.id), types["Late arrival or leaving early"])
    third = await _warn(app_client, "EMPLOYEE", str(emp.id), types["Negligence of duty"])
    assert third["is_final"]
    # final chance: nothing changes, but the next warning is final again
    assert (await app_client.post(f"{API}/warnings/{third['id']}/decide", json={"action": "NONE", "note": "Last chance given after a meeting."})).status_code == 200
    fourth = await _warn(app_client, "EMPLOYEE", str(emp.id), types["Negligence of duty"])
    assert fourth["number"] == 4 and fourth["is_final"]
    # a fine needs an amount, and becomes a real fine on the staff member
    assert (await app_client.post(f"{API}/warnings/{fourth['id']}/decide", json={"action": "FINE", "note": "Fined after the second inquiry."})).status_code == 422
    f = await app_client.post(f"{API}/warnings/{fourth['id']}/decide", json={"action": "FINE", "note": "Fined after the second inquiry.", "amount": 2500})
    assert f.status_code == 200, f.text
    w = await db_session.get(DisciplinaryWarning, uuid.UUID(fourth["id"]))
    await db_session.refresh(w)
    fine = await db_session.get(Fine, w.fine_id)
    assert float(fine.amount) == 2500 and fine.payer_id == emp.id and fine.source.value == "DISCIPLINARY" and float(w.fine_amount) == 2500
    assert (await app_client.post(f"{API}/warnings/{fourth['id']}/withdraw", json={"note": "Changed our mind"})).status_code == 422

    # the next one: suspension. On leave, and the staff member cannot sign in until it is ended
    fifth = await _warn(app_client, "EMPLOYEE", str(emp.id), types["Harassment"])
    assert fifth["is_final"]
    assert (await app_client.post(f"{API}/warnings/{fifth['id']}/decide", json={"action": "SUSPEND", "note": "Suspended pending the committee."})).status_code == 200
    await db_session.refresh(emp)
    user = await db_session.get(User, emp.user_id)
    await db_session.refresh(user)
    assert emp.status.value == "ON_LEAVE" and user.is_active is False
    office = (await app_client.get(f"{API}/warnings", params={"person_id": str(emp.id)})).json()["warnings"]
    assert next(x for x in office if x["id"] == fifth["id"])["can_lift"] is True
    assert (await app_client.post(f"{API}/warnings/{fifth['id']}/lift", json={"note": "Committee cleared the matter."})).status_code == 200
    await db_session.refresh(emp)
    await db_session.refresh(user)
    assert emp.status.value == "ACTIVE" and user.is_active is True
