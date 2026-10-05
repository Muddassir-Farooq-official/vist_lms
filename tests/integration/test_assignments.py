"""Assignments: set by the class's teacher, handed in as a Google Drive link before the deadline, accepted or rejected."""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from sqlalchemy import func, select, update

from app.assignments import drive
from app.assignments.models import Assignment
from app.core.config import settings
from app.notifications.models import Notification, NotificationCategory
from tests.integration.test_attendance_and_grading import _login, _login_admin, _setup_offering_with_enrolled_students
from tests.integration.test_module3_sections_allocation import _teacher
from tests.integration.test_teacher_content_and_bulk_timetable import _sign_in_teacher, _teacher_session

pytestmark = pytest.mark.asyncio
API = "/api/v1/assignments"
GOOD = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view?usp=sharing"
PW = "StudentPass123!"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    monkeypatch.setattr(settings, "assignment_drive_access_check", False)          # no internet in tests


def _in(hours):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


async def _class(app_client, db_session, code="ASG1", n=3):
    offering_id, studs = await _setup_offering_with_enrolled_students(app_client, code=code, n_students=n)
    _tid, teacher = await _teacher_session(app_client, db_session, "ASGT", "35202-9910001-1", offering_id)
    return offering_id, studs, teacher


async def _teacher_in(app_client, username, first_time=False):
    if first_time:
        await _sign_in_teacher(app_client, username)
    else:
        await _login(app_client, username, "TeacherNew12345!")


async def _set(app_client, offering_id, **kw):
    body = {"course_offering_id": offering_id, "title": "Lab report 1", "instructions": "Write up the experiment and share a Drive link.", "deadline": _in(48)}
    body.update(kw)
    r = await app_client.post(API, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _expire(db_session, assignment_id, hours_ago=1):
    await db_session.execute(update(Assignment).where(Assignment.id == assignment_id).values(deadline=datetime.now(timezone.utc) - timedelta(hours=hours_ago)))
    await db_session.commit()


async def test_a_teacher_sets_assignments_only_for_their_own_class_with_a_future_deadline(app_client, admin_user, db_session):
    offering_id, studs, teacher = await _class(app_client, db_session)
    other_id = await _teacher(app_client, db_session, "OTHR", "35202-9910002-2")
    other = (await app_client.get(f"/api/v1/employees/{other_id}")).json()["username"]
    await _teacher_in(app_client, teacher, first_time=True)
    past = await app_client.post(API, json={"course_offering_id": offering_id, "title": "Late one", "instructions": "x" * 10, "deadline": _in(-1)})
    assert past.status_code == 422 and "future" in past.text
    made = await _set(app_client, offering_id)
    assert made["is_open"] is True and made["enrolled"] == 3 and made["submitted"] == 0 and made["not_submitted"] == 3
    await _teacher_in(app_client, other, first_time=True)
    assert (await app_client.post(API, json={"course_offering_id": offering_id, "title": "Not mine", "instructions": "x" * 10, "deadline": _in(5)})).status_code == 403
    await _login(app_client, studs[0][1], PW)
    assert (await app_client.post(API, json={"course_offering_id": offering_id, "title": "Sneaky", "instructions": "x" * 10, "deadline": _in(5)})).status_code == 403
    assert (await app_client.get(f"{API}/teaching")).status_code == 403
    app_client.cookies.clear()
    assert (await app_client.post(API, json={})).status_code in (401, 403)


async def test_setting_an_assignment_notifies_exactly_the_students_in_the_class(app_client, admin_user, db_session):
    offering_id, studs, teacher = await _class(app_client, db_session)
    await _teacher_in(app_client, teacher, first_time=True)
    made = await _set(app_client, offering_id, title="Essay on trees")
    notes = (await db_session.execute(select(Notification).where(Notification.category == NotificationCategory.ASSIGNMENT))).scalars().all()
    assert len(notes) == 3
    assert all(n.title == "New assignment: Essay on trees" and n.link == f"/dashboard/student/assignments?open={made['id']}" and "due" in n.message for n in notes)


async def test_students_see_assignments_for_their_classes_only(app_client, admin_user, db_session):
    offering_id, studs, teacher = await _class(app_client, db_session)
    await _teacher_in(app_client, teacher, first_time=True)
    made = await _set(app_client, offering_id)
    await _login(app_client, studs[1][1], PW)
    mine = (await app_client.get(f"{API}/mine")).json()
    assert len(mine) == 1 and mine[0]["id"] == made["id"] and mine[0]["state"] == "NOT_SUBMITTED" and mine[0]["can_submit"] is True
    assert mine[0]["course_code"].startswith("ASG1") and mine[0]["teacher_name"] and mine[0]["submission"] is None
    # a student who is not in the class sees nothing and cannot hand in
    await _login_admin(app_client)
    stranger = await app_client.post("/api/v1/students", json={
        "first_name": "Out", "last_name": "Sider", "date_of_birth": "2005-03-03", "gender": "Male", "cnic": "35202-9910003-3", "contact_no": "03001110000",
        "marital_status": "Single", "religion": "Islam", "nationality": "Pakistani", "guardian_name": "G", "guardian_cnic": "35202-9910004-4",
        "relation_with_guardian": "Father", "emergency_contact_no": "03001110001", "present_address": "x", "province": "Punjab", "district": "Lahore", "city": "Lahore"})
    assert stranger.status_code == 201
    await _login(app_client, stranger.json()["username"], "vertex123")
    assert (await app_client.post("/api/v1/auth/change-password", json={"current_password": "vertex123", "new_password": "Stranger#Pass2026"})).status_code == 204
    assert (await app_client.get(f"{API}/mine")).json() == []
    assert (await app_client.post(f"{API}/{made['id']}/submit", json={"drive_url": GOOD})).status_code == 404


@pytest.mark.parametrize("url", [
    "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view?usp=sharing",
    "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345",
    "https://drive.google.com/drive/u/0/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345?usp=sharing",
    "https://drive.google.com/open?id=1AbCdEfGhIjKlMnOpQrStUvWxYz012345",
    "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit?usp=sharing",
    "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit#gid=0",
    "https://docs.google.com/presentation/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit",
    "  https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view  ",
])
async def test_real_google_links_are_accepted(url):
    assert drive.normalise(url).startswith("https://")


@pytest.mark.parametrize("url", [
    "", "   ", "http://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view", "https://example.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345",
    "https://drive.google.com.evil.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view", "https://evil.com/?u=https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345",
    "javascript:alert(1)", "https://drive.google.com/", "https://drive.google.com/file/d/x/view", "https://docs.google.com/notadoc/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345",
    "https://user:pw@drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view", "https://drive.google.com/open?id=short", "https://dropbox.com/s/abc/file.pdf",
])
async def test_anything_that_is_not_a_google_drive_link_is_refused(url):
    from app.core.exceptions import BusinessRuleViolationError

    with pytest.raises(BusinessRuleViolationError):
        drive.normalise(url)


async def test_submitting_stores_the_link_and_a_bad_link_is_refused_politely(app_client, admin_user, db_session):
    offering_id, studs, teacher = await _class(app_client, db_session)
    await _teacher_in(app_client, teacher, first_time=True)
    a = await _set(app_client, offering_id)
    await _login(app_client, studs[0][1], PW)
    bad = await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": "https://example.com/mywork"})
    assert bad.status_code == 422 and "Google Drive" in bad.json()["error"]["message"]
    ok = await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": GOOD, "note": "Sorry, the last page is a photo."})
    assert ok.status_code == 200
    sub = ok.json()["submission"]
    assert sub["status"] == "SUBMITTED" and sub["drive_url"] == GOOD and sub["note"].startswith("Sorry") and sub["resubmission_count"] == 0
    assert ok.json()["state"] == "SUBMITTED" and ok.json()["can_submit"] is True                 # may still replace it before the deadline


async def test_a_link_that_looks_private_warns_the_student_who_may_still_insist(app_client, admin_user, db_session, monkeypatch):
    offering_id, studs, teacher = await _class(app_client, db_session)
    await _teacher_in(app_client, teacher, first_time=True)
    a = await _set(app_client, offering_id)

    async def private(_url):
        return "private"

    monkeypatch.setattr(drive, "check_public_access", private)
    monkeypatch.setattr(settings, "assignment_drive_access_check", True)
    await _login(app_client, studs[0][1], PW)
    warn = await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": GOOD})
    assert warn.status_code == 422 and warn.json()["error"]["code"] == "DRIVE_LINK_PRIVATE" and "Anyone with the link" in warn.json()["error"]["message"]
    assert (await app_client.get(f"{API}/mine")).json()[0]["submission"] is None                  # nothing was saved
    insist = await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": GOOD, "acknowledge_private": True})
    assert insist.status_code == 200 and insist.json()["submission"]["status"] == "SUBMITTED"


async def test_the_access_check_reads_google_sign_in_redirects_as_private_and_never_blocks_on_trouble():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/private"):
                self.send_response(302); self.send_header("Location", "https://accounts.google.com/ServiceLogin?x=1"); self.end_headers()
            elif self.path.startswith("/public"):
                self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
            else:
                self.send_response(404); self.end_headers()

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert await asyncio.to_thread(drive._probe, base + "/private") == "private"
        assert await asyncio.to_thread(drive._probe, base + "/public") == "public"
        assert await asyncio.to_thread(drive._probe, base + "/missing") == "unknown"
    finally:
        server.shutdown()
    assert await asyncio.to_thread(drive._probe, "http://127.0.0.1:9/never") == "unknown"        # nothing listening: unknown, not an error


async def test_after_the_deadline_nobody_can_submit_or_change_a_submission(app_client, admin_user, db_session):
    offering_id, studs, teacher = await _class(app_client, db_session)
    await _teacher_in(app_client, teacher, first_time=True)
    a = await _set(app_client, offering_id)
    await _login(app_client, studs[0][1], PW)
    assert (await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": GOOD})).status_code == 200
    await _expire(db_session, a["id"])
    # the one who had submitted cannot replace it, the others cannot submit at all
    late = await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": GOOD.replace("Yz0", "Xx0")})
    assert late.status_code == 422 and "deadline has passed" in late.json()["error"]["message"]
    await _login(app_client, studs[1][1], PW)
    assert (await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": GOOD})).status_code == 422
    row = (await app_client.get(f"{API}/mine")).json()[0]
    assert (row["state"], row["can_submit"], row["is_open"]) == ("MISSED", False, False)
    await _login(app_client, studs[0][1], PW)
    first = (await app_client.get(f"{API}/mine")).json()[0]
    assert first["state"] == "SUBMITTED" and first["can_submit"] is False and first["submission"]["drive_url"] == GOOD       # their work is untouched


async def test_the_teacher_can_still_review_after_the_deadline_and_can_reopen_or_close_it(app_client, admin_user, db_session):
    offering_id, studs, teacher = await _class(app_client, db_session)
    await _teacher_in(app_client, teacher, first_time=True)
    a = await _set(app_client, offering_id)
    await _login(app_client, studs[0][1], PW)
    sub = (await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": GOOD})).json()["submission"]
    await _expire(db_session, a["id"])
    await _teacher_in(app_client, teacher)
    assert (await app_client.post(f"{API}/submissions/{sub['id']}/review", json={"decision": "ACCEPTED"})).status_code == 200      # review is not blocked
    # extending the deadline reopens it for everyone
    reopened = await app_client.patch(f"{API}/{a['id']}", json={"deadline": _in(24)})
    assert reopened.status_code == 200 and reopened.json()["is_open"] is True
    await _login(app_client, studs[1][1], PW)
    assert (await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": GOOD})).status_code == 200
    # closing it now stops submissions at once
    await _teacher_in(app_client, teacher)
    closed = await app_client.post(f"{API}/{a['id']}/close")
    assert closed.status_code == 200 and closed.json()["is_open"] is False
    await _login(app_client, studs[2][1], PW)
    assert (await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": GOOD})).status_code == 422


async def test_reject_with_a_reason_then_resubmit_then_accept(app_client, admin_user, db_session):
    offering_id, studs, teacher = await _class(app_client, db_session)
    await _teacher_in(app_client, teacher, first_time=True)
    a = await _set(app_client, offering_id, title="Lab report 1")
    await _login(app_client, studs[0][1], PW)
    first = (await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": GOOD})).json()["submission"]
    await _teacher_in(app_client, teacher)
    assert (await app_client.post(f"{API}/submissions/{first['id']}/review", json={"decision": "REJECTED"})).status_code == 422             # a reason is required
    assert (await app_client.post(f"{API}/submissions/{first['id']}/review", json={"decision": "REJECTED", "feedback": "  "})).status_code == 422
    rej = await app_client.post(f"{API}/submissions/{first['id']}/review", json={"decision": "REJECTED", "feedback": "The link opens an empty folder."})
    assert rej.status_code == 200 and rej.json()["status"] == "REJECTED" and rej.json()["feedback"] == "The link opens an empty folder."
    # the student sees it, and is told
    await _login(app_client, studs[0][1], PW)
    seen = (await app_client.get(f"{API}/mine")).json()[0]
    assert seen["state"] == "REJECTED" and seen["can_submit"] is True and seen["submission"]["feedback"].startswith("The link opens")
    notes = (await db_session.execute(select(Notification).where(Notification.title == "Assignment needs changes"))).scalars().all()
    assert len(notes) == 1 and "empty folder" in notes[0].message
    again = (await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": GOOD.replace("Yz0", "Aa0")})).json()
    assert again["state"] == "SUBMITTED" and again["submission"]["resubmission_count"] == 1 and again["submission"]["feedback"] is None and again["submission"]["reviewed_at"] is None
    await _teacher_in(app_client, teacher)
    assert (await app_client.post(f"{API}/submissions/{first['id']}/review", json={"decision": "ACCEPTED", "feedback": "Good."})).json()["status"] == "ACCEPTED"
    # accepted work is final for the student
    await _login(app_client, studs[0][1], PW)
    final = await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": GOOD})
    assert final.status_code == 422 and "already accepted" in final.json()["error"]["message"]
    assert (await app_client.get(f"{API}/mine")).json()[0]["can_submit"] is False


async def test_the_class_list_shows_everyone_with_the_ones_to_review_first(app_client, admin_user, db_session):
    offering_id, studs, teacher = await _class(app_client, db_session, n=4)
    await _teacher_in(app_client, teacher, first_time=True)
    a = await _set(app_client, offering_id)
    ids = {}
    for i in (0, 1, 2):
        await _login(app_client, studs[i][1], PW)
        ids[i] = (await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": GOOD})).json()["submission"]["id"]
    await _teacher_in(app_client, teacher)
    await app_client.post(f"{API}/submissions/{ids[1]}/review", json={"decision": "ACCEPTED"})
    await app_client.post(f"{API}/submissions/{ids[2]}/review", json={"decision": "REJECTED", "feedback": "Wrong file."})
    rows = (await app_client.get(f"{API}/{a['id']}/submissions")).json()
    assert [r["state"] for r in rows] == ["SUBMITTED", "NOT_SUBMITTED", "REJECTED", "ACCEPTED"]            # waiting for review, then missing, then the rest
    assert rows[0]["drive_url"] == GOOD and rows[1]["drive_url"] is None and len({r["student_id"] for r in rows}) == 4
    summary = (await app_client.get(f"{API}/teaching")).json()[0]
    assert (summary["enrolled"], summary["submitted"], summary["awaiting_review"], summary["accepted"], summary["rejected"], summary["not_submitted"]) == (4, 3, 1, 1, 1, 1)


async def test_other_people_cannot_see_review_or_change_someone_elses_work(app_client, admin_user, db_session):
    offering_id, studs, teacher = await _class(app_client, db_session)
    other_id = await _teacher(app_client, db_session, "OTHR", "35202-9910005-5")
    other = (await app_client.get(f"/api/v1/employees/{other_id}")).json()["username"]
    await _teacher_in(app_client, teacher, first_time=True)
    a = await _set(app_client, offering_id)
    await _login(app_client, studs[0][1], PW)
    sub = (await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": GOOD})).json()["submission"]
    # a classmate sees only their own state, and cannot review
    await _login(app_client, studs[1][1], PW)
    assert (await app_client.get(f"{API}/mine")).json()[0]["submission"] is None
    assert (await app_client.post(f"{API}/submissions/{sub['id']}/review", json={"decision": "ACCEPTED"})).status_code == 403
    assert (await app_client.get(f"{API}/{a['id']}/submissions")).status_code == 403
    # a teacher of some other class can neither list nor review nor change
    await _teacher_in(app_client, other, first_time=True)
    assert (await app_client.get(f"{API}/{a['id']}/submissions")).status_code == 403
    assert (await app_client.post(f"{API}/submissions/{sub['id']}/review", json={"decision": "ACCEPTED"})).status_code == 403
    assert (await app_client.patch(f"{API}/{a['id']}", json={"title": "Hijacked"})).status_code == 403
    assert (await app_client.delete(f"{API}/{a['id']}")).status_code == 403
    assert (await app_client.get(f"{API}/teaching")).json() == []


async def test_an_assignment_with_work_handed_in_cannot_be_deleted(app_client, admin_user, db_session):
    offering_id, studs, teacher = await _class(app_client, db_session)
    await _teacher_in(app_client, teacher, first_time=True)
    unused, used = await _set(app_client, offering_id, title="Unused"), await _set(app_client, offering_id, title="Used")
    await _login(app_client, studs[0][1], PW)
    await app_client.post(f"{API}/{used['id']}/submit", json={"drive_url": GOOD})
    await _teacher_in(app_client, teacher)
    assert (await app_client.delete(f"{API}/{unused['id']}")).status_code == 204
    blocked = await app_client.delete(f"{API}/{used['id']}")
    assert blocked.status_code == 422 and "Close it instead" in blocked.json()["error"]["message"]
    assert {r["title"] for r in (await app_client.get(f"{API}/teaching")).json()} == {"Used"}


async def test_a_student_who_leaves_the_class_drops_out_of_it(app_client, admin_user, db_session):
    offering_id, studs, teacher = await _class(app_client, db_session)
    await _teacher_in(app_client, teacher, first_time=True)
    a = await _set(app_client, offering_id)
    await _login(app_client, studs[0][1], PW)
    await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": GOOD})
    await _login_admin(app_client)
    assert (await app_client.post(f"/api/v1/academics/enrollments/{studs[0][2]}/admin-withdraw")).status_code == 200
    await _teacher_in(app_client, teacher)
    summary = (await app_client.get(f"{API}/teaching")).json()[0]
    assert (summary["enrolled"], summary["submitted"]) == (2, 0)
    assert studs[0][0] not in {r["student_id"] for r in (await app_client.get(f"{API}/{a['id']}/submissions")).json()}
    await _login(app_client, studs[0][1], PW)
    assert (await app_client.get(f"{API}/mine")).json() == []
    assert (await app_client.post(f"{API}/{a['id']}/submit", json={"drive_url": GOOD})).status_code == 404