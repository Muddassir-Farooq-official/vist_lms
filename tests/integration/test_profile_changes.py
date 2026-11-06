"""Students and employees ask to change their contact details and photo; an administrator approves or rejects."""
from __future__ import annotations

from pathlib import Path

from app.selfservice.face_check import check_face
from tests.integration.test_file_download_authorization import (STUDENT_PASSWORD, TEACHER_PASSWORD, _create_and_activate_student,
                                                                _create_teacher, _login, _login_admin)

P = "/api/v1/profile-changes"
FIX = Path(__file__).resolve().parent.parent / "fixtures"


def _photo(name: str) -> bytes:
    return (FIX / name).read_bytes()


def test_face_check_accepts_a_clear_face_and_refuses_covered_ones():
    assert check_face(_photo("face_clear.jpg")).ok
    masked = check_face(_photo("face_masked.jpg"))
    assert not masked.ok and "covered" in masked.reason
    assert not check_face(_photo("face_sunglasses.jpg")).ok
    assert not check_face(b"not an image").ok


async def test_student_asks_admin_approves_and_it_goes_live(app_client, admin_user, db_session):
    _sid, username = await _create_and_activate_student(app_client, cnic="93001-0000001-1")
    me = (await app_client.get(f"{P}/me")).json()
    assert me["kind"] == "STUDENT" and "guardian_contact_no" in {f["key"] for f in me["fields"]}
    old_phone = me["values"]["contact_no"]

    # Office-only details are refused; bad values are refused; nothing changes until approved.
    r = await app_client.post(f"{P}/me", json={"values": {"roll_number": "X"}})                 # the roll number never changes
    assert r.status_code == 422 and "office" in r.json()["error"]["message"]
    assert (await app_client.post(f"{P}/me", json={"values": {"contact_no": "abc"}})).status_code == 422
    r = await app_client.post(f"{P}/me", json={"values": {"contact_no": "0301-7654321", "email": "me@example.pk"}, "note": "New SIM"})
    assert r.status_code == 200 and {c["field"] for c in r.json()["changes"]} == {"contact_no", "email"}
    assert (await app_client.get("/api/v1/students/me")).json()["contact_no"] == old_phone

    # Photos: a covered face never reaches the office; a clear one waits.
    bad = await app_client.post("/api/v1/students/me/photo", files={"file": ("m.jpg", _photo("face_masked.jpg"), "image/jpeg")})
    assert bad.status_code == 422 and "covered" in bad.json()["error"]["message"]
    good = await app_client.post("/api/v1/students/me/photo", files={"file": ("me.jpg", _photo("face_clear.jpg"), "image/jpeg")})
    assert good.status_code == 202 and good.json()["pending_approval"] is True
    assert (await app_client.get("/api/v1/students/me/photo")).status_code == 404       # not published yet
    assert (await app_client.get(f"{P}/me/photo")).status_code == 200                     # but the person can see what they sent

    await _login_admin(app_client)
    assert (await app_client.get(f"{P}/count")).json()["waiting"] == 1
    req = (await app_client.get(P)).json()[0]
    assert req["subject"]["code"] == username and req["has_photo"] and req["photo_check"]["checked"]
    assert (await app_client.post(f"{P}/{req['id']}/reject", json={"reason": " "})).status_code == 422      # a reason is required
    approved = await app_client.post(f"{P}/{req['id']}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"
    assert (await app_client.post(f"{P}/{req['id']}/approve")).status_code == 422                 # decided once only

    await _login(app_client, username, STUDENT_PASSWORD)
    assert (await app_client.get("/api/v1/students/me")).json()["contact_no"] == "0301-7654321"
    assert (await app_client.get("/api/v1/students/me/photo")).status_code == 200
    mine = (await app_client.get(f"{P}/me")).json()
    assert mine["pending"] is None and mine["history"][0]["status"] == "APPROVED"
    notes = (await app_client.get("/api/v1/notifications/me")).json()
    assert any("approved" in n["title"] for n in notes)

    await _login_admin(app_client)
    log = (await app_client.get("/api/v1/audit/activity", params={"action": "profile_change"})).json()
    actions = {x["action"] for x in log["items"]}
    assert {"profile_change.requested", "profile_change.photo_refused", "profile_change.photo_requested", "profile_change.approved"} <= actions
    approved_entry = next(x for x in log["items"] if x["action"] == "profile_change.approved")
    detail = (await app_client.get(f"/api/v1/audit/activity/{approved_entry['id']}")).json()
    assert detail["before"]["contact_no"] == old_phone and detail["after"]["contact_no"] == "0301-7654321"


async def test_employee_rejection_with_reason_and_withdraw(app_client, admin_user, db_session):
    _eid, username = await _create_teacher(app_client, db_session, code="T-PC", cnic="83001-0000001-1")
    await _login(app_client, username, TEACHER_PASSWORD)
    r = await app_client.post(f"{P}/me", json={"values": {"emergency_contact_no": "0333-1112223", "emergency_contact_name": "Brother"}})
    assert r.status_code == 200
    await _login_admin(app_client)
    rid = (await app_client.get(P)).json()[0]["id"]
    assert (await app_client.post(f"{P}/{rid}/reject", json={"reason": "Please give a working number."})).status_code == 200
    await _login(app_client, username, TEACHER_PASSWORD)
    mine = (await app_client.get(f"{P}/me")).json()
    assert mine["values"]["emergency_contact_no"] in (None, "") and mine["history"][0]["decision_reason"] == "Please give a working number."

    # A second request can be withdrawn by the person while it waits.
    rid2 = (await app_client.post(f"{P}/me", json={"values": {"city": "Gilgit"}})).json()["id"]
    assert (await app_client.post(f"{P}/me/{rid2}/withdraw")).json()["status"] == "CANCELLED"
    assert (await app_client.post(f"{P}/me/{rid2}/withdraw")).status_code == 422


async def test_only_the_administration_decides(app_client, admin_user, db_session):
    _sid, username = await _create_and_activate_student(app_client, cnic="93002-0000001-1")
    await app_client.post(f"{P}/me", json={"values": {"city": "Skardu"}})
    assert (await app_client.get(P)).status_code == 403
    assert (await app_client.get(f"{P}/count")).status_code == 403
    await _login_admin(app_client)
    assert (await app_client.get("/dashboard/admin/profile-changes")).status_code == 200
    assert (await app_client.get(f"{P}/me")).status_code == 422                     # the administrator has no student / staff profile


async def test_a_student_asks_to_correct_name_cnic_and_guardian_and_the_office_approves(app_client, admin_user, db_session):
    sid, username = await _create_and_activate_student(app_client, cnic="93001-0000009-1")
    other, _ = await _create_and_activate_student(app_client, cnic="93001-0000010-1")
    await _login(app_client, username, STUDENT_PASSWORD)
    me = (await app_client.get(f"{P}/me")).json()
    keys = {f["key"] for f in me["fields"]}
    assert {"first_name", "last_name", "date_of_birth", "gender", "cnic", "nationality", "guardian_name", "guardian_cnic"} <= keys
    assert "roll_number" not in keys
    assert (await app_client.post(f"{P}/me", json={"values": {"cnic": "123"}})).status_code == 422
    assert (await app_client.post(f"{P}/me", json={"values": {"date_of_birth": "2999-01-01"}})).status_code == 422
    r = await app_client.post(f"{P}/me", json={"values": {"first_name": "Ayesha", "cnic": "9300100000111", "date_of_birth": "2004-05-06",
                                                          "guardian_name": "Imran Khan", "guardian_cnic": "93001-0000012-1"},
                                               "note": "My name and CNIC were typed wrong"})
    assert r.status_code == 200, r.text
    req_id = r.json()["id"]
    assert {c["field"] for c in r.json()["changes"]} == {"first_name", "cnic", "date_of_birth", "guardian_name", "guardian_cnic"}
    assert (await app_client.get("/api/v1/students/me")).json()["first_name"] != "Ayesha"         # nothing changes before approval

    await _login_admin(app_client)
    roll_before = (await app_client.get(f"/api/v1/students/{sid}")).json()["roll_number"]
    ok = await app_client.post(f"{P}/{req_id}/approve", json={})
    assert ok.status_code == 200, ok.text
    st = (await app_client.get(f"/api/v1/students/{sid}")).json()
    assert st["first_name"] == "Ayesha" and st["cnic"] == "93001-0000011-1" and st["date_of_birth"] == "2004-05-06"
    assert st["roll_number"] == roll_before                                                      # the roll number is untouched

    # a CNIC that belongs to another student is stopped at approval
    await _login(app_client, username, STUDENT_PASSWORD)
    other_cnic = "93001-0000010-1"
    r2 = (await app_client.post(f"{P}/me", json={"values": {"cnic": other_cnic}})).json()
    await _login_admin(app_client)
    bad = await app_client.post(f"{P}/{r2['id']}/approve", json={})
    assert bad.status_code == 422 and "Another student" in bad.text