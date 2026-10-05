"""Official announcements: issuer, audience, attachments, notifications, read tracking, and who may do what."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.employees.models import JobRole
from app.notifications.models import Notification, NotificationCategory
from tests.integration.test_module3_sections_allocation import _program

pytestmark = pytest.mark.asyncio
ADMIN = {"username": "admin_test", "password": "AdminPass123!"}
API = "/api/v1/announcements"


@pytest.fixture(autouse=True)
def _no_login_throttle(monkeypatch):
    from app.core.config import settings as s

    monkeypatch.setattr(s, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(s, "rate_limit_login_per_username_per_hour", 1000)


async def _login(client, username, password):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text


async def _first_login(client, username, first):
    await _login(client, username, first)
    new = "Chosen#Pass-2026x"
    assert (await client.post("/api/v1/auth/change-password", json={"current_password": first, "new_password": new})).status_code == 204
    return new


async def _student(client, n, program_id=None):
    body = {"first_name": f"Stu{n}", "last_name": "Dent", "date_of_birth": "2005-03-03", "gender": "Female", "cnic": f"35202-61000{n:02d}-1",
            "contact_no": f"0300666{n:04d}", "marital_status": "Single", "religion": "Islam", "nationality": "Pakistani", "guardian_name": "G",
            "guardian_cnic": f"35202-62000{n:02d}-2", "relation_with_guardian": "Father", "emergency_contact_no": "03001110001",
            "present_address": "x", "province": "Punjab", "district": "Lahore", "city": "Lahore"}
    if program_id:
        body.update(program_id=program_id, current_semester=1)
    r = await client.post("/api/v1/students", json=body)
    assert r.status_code == 201, r.text
    return r.json()["username"], "vertex123"


async def _staff(client, db, code, system_role, cnic):
    jr = JobRole(code=code, title=code, default_role_code=system_role)
    db.add(jr)
    await db.commit()
    r = await client.post("/api/v1/employees", json={"first_name": code, "last_name": "X", "date_of_birth": "1985-01-01", "gender": "Male", "cnic": cnic,
        "contact_no": "03007770000", "hire_date": "2026-01-01", "job_role_id": str(jr.id), "base_salary": 1, "password": "FirstPass#12345"})
    assert r.status_code == 201, r.text
    return r.json()["username"], "FirstPass#12345"


async def _authority(client, name="Dr. A. Khan", designation="Managing Director"):
    r = await client.post(f"{API}/authorities", json={"name": name, "designation": designation})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _announce(client, authority_id, **kw):
    body = {"title": "Eid holidays", "body": "The institute will remain closed from Monday.\nClasses resume on Thursday.", "issuer_id": authority_id, "publish": True}
    body.update(kw)
    r = await client.post(API, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _docx(valid=True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        if valid:
            z.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def _people(client, db):
    """admin + a BSCS student, a BBA student, a teacher, a librarian, an accounts officer -- each able to sign in."""
    await _login(client, **ADMIN)
    bscs, bba = await _program(db, "BSCS"), await _program(db, "BBA")
    raw = {"s_bscs": await _student(client, 1, str(bscs.id)), "s_bba": await _student(client, 2, str(bba.id)),
           "teacher": await _staff(client, db, "TCH", "TEACHER", "35202-6300001-1"),
           "librarian": await _staff(client, db, "LIB", "LIBRARIAN", "35202-6300002-2"),
           "accounts": await _staff(client, db, "ACC", "ACCOUNTS_OFFICER", "35202-6300003-3")}
    return bscs, bba, raw


async def _as(client, raw, who):
    username, first = raw[who]
    if who not in _as.cache:
        _as.cache[who] = await _first_login(client, username, first)
        return
    await _login(client, username, _as.cache[who])


_as.cache = {}


@pytest.fixture(autouse=True)
def _reset_cache():
    _as.cache = {}


async def _feed(client):
    r = await client.get(API)
    assert r.status_code == 200, r.text
    return {a["title"]: a for a in r.json()}


async def test_only_the_admin_manages_announcements_and_issuing_officials(app_client, admin_user, db_session):
    _b1, _b2, raw = await _people(app_client, db_session)
    aid = await _authority(app_client)
    assert (await app_client.get(f"{API}/authorities")).json()[0]["designation"] == "Managing Director"
    for who in ("s_bscs", "teacher", "librarian"):
        await _as(app_client, raw, who)
        assert (await app_client.post(API, json={"title": "Hello", "body": "x", "issuer_id": aid})).status_code == 403
        assert (await app_client.post(f"{API}/authorities", json={"name": "Me", "designation": "Boss"})).status_code == 403
        assert (await app_client.get(f"{API}/manage")).status_code == 403
    await _login(app_client, **ADMIN)
    a = await _announce(app_client, aid)
    await _as(app_client, raw, "s_bscs")
    for call in (app_client.post(f"{API}/{a['id']}/archive"), app_client.delete(f"{API}/{a['id']}"), app_client.post(f"{API}/{a['id']}/publish")):
        assert (await call).status_code == 403


async def test_the_issuer_is_recorded_as_a_snapshot_and_must_be_active(app_client, admin_user, db_session):
    _b1, _b2, raw = await _people(app_client, db_session)
    aid = await _authority(app_client, "Prof. R. Ali", "Principal")
    a = await _announce(app_client, aid, title="Fee schedule")
    assert (a["issuer_name"], a["issuer_designation"]) == ("Prof. R. Ali", "Principal")
    assert (await app_client.patch(f"{API}/authorities/{aid}", json={"designation": "Rector"})).status_code == 200
    again = (await app_client.get(f"{API}/{a['id']}")).json()
    assert again["issuer_designation"] == "Principal"                         # an old announcement keeps who issued it at the time
    assert (await app_client.patch(f"{API}/authorities/{aid}", json={"is_active": False})).status_code == 200
    bad = await app_client.post(API, json={"title": "Another", "body": "x", "issuer_id": aid})
    assert bad.status_code == 422 and "active issuing authority" in bad.text
    nobody = await app_client.post(API, json={"title": "Nobody", "body": "x", "issuer_id": aid, "audience_students": False, "audience_teachers": False, "audience_staff": False})
    assert nobody.status_code == 422


async def test_drafts_are_private_and_publishing_notifies_exactly_the_audience(app_client, admin_user, db_session):
    _b1, _b2, raw = await _people(app_client, db_session)
    aid = await _authority(app_client)
    draft = await _announce(app_client, aid, title="Not yet", publish=False)
    assert draft["status"] == "DRAFT"
    for who in ("s_bscs", "teacher", "librarian"):
        await _as(app_client, raw, who)
        assert "Not yet" not in await _feed(app_client)
        assert (await app_client.get(f"{API}/{draft['id']}")).status_code == 404            # a draft simply isn't there for them
    await _login(app_client, **ADMIN)
    before = (await db_session.execute(select(func.count()).select_from(Notification).where(Notification.category == NotificationCategory.ANNOUNCEMENT))).scalar_one()
    assert before == 0
    pub = await app_client.post(f"{API}/{draft['id']}/publish")
    assert pub.status_code == 200 and pub.json()["status"] == "PUBLISHED"
    notes = (await db_session.execute(select(Notification).where(Notification.category == NotificationCategory.ANNOUNCEMENT))).scalars().all()
    assert len(notes) == 5                                                                     # 2 students + teacher + librarian + accounts officer
    # published once more or edited: nobody is notified a second time
    assert (await app_client.post(f"{API}/{draft['id']}/publish")).status_code == 200
    assert (await app_client.patch(f"{API}/{draft['id']}", json={"title": "Not yet (corrected)"})).status_code == 200
    again = (await db_session.execute(select(func.count()).select_from(Notification).where(Notification.category == NotificationCategory.ANNOUNCEMENT))).scalar_one()
    assert again == 5


async def test_the_audience_decides_who_sees_it_and_who_is_notified(app_client, admin_user, db_session):
    bscs, _bba, raw = await _people(app_client, db_session)
    aid = await _authority(app_client)
    await _announce(app_client, aid, title="For students", audience_teachers=False, audience_staff=False)
    await _announce(app_client, aid, title="For staff", audience_students=False, audience_teachers=False)
    await _announce(app_client, aid, title="For teachers", audience_students=False, audience_staff=False)
    await _announce(app_client, aid, title="BSCS only", audience_teachers=False, audience_staff=False, program_ids=[str(bscs.id)])
    await _announce(app_client, aid, title="For everyone")
    expect = {
        "s_bscs": {"For students", "BSCS only", "For everyone"}, "s_bba": {"For students", "For everyone"},
        "teacher": {"For teachers", "For everyone"}, "librarian": {"For staff", "For everyone"}, "accounts": {"For staff", "For everyone"},
    }
    for who, titles in expect.items():
        await _as(app_client, raw, who)
        assert set(await _feed(app_client)) == titles, who
        assert (await app_client.get(f"{API}/unread-count")).json()["unread_count"] == len(titles), who
    # the bell notifications match the audience too (one per person per announcement)
    await _login(app_client, **ADMIN)
    per_title = {t: n for t, n in (await db_session.execute(select(Notification.title, func.count()).where(Notification.category == NotificationCategory.ANNOUNCEMENT).group_by(Notification.title))).all()}
    assert per_title == {"For students": 2, "For staff": 2, "For teachers": 1, "BSCS only": 1, "For everyone": 5}


async def test_notifications_carry_the_official_the_priority_and_a_link_to_the_right_portal(app_client, admin_user, db_session):
    _b1, _b2, raw = await _people(app_client, db_session)
    aid = await _authority(app_client, "Dr. A. Khan", "Managing Director")
    a = await _announce(app_client, aid, title="Campus closed", priority="URGENT", body="Heavy rain: the campus is closed tomorrow.")
    from app.auth.models import User

    expected = {"s_bscs": "/dashboard/student/announcements", "teacher": "/dashboard/faculty/announcements",
                "librarian": "/dashboard/library/announcements", "accounts": "/dashboard/accounts/announcements"}
    for who, base in expected.items():
        username = raw[who][0]
        uid = (await db_session.execute(select(User.id).where(User.username == username))).scalar_one()
        note = (await db_session.execute(select(Notification).where(Notification.user_id == uid))).scalars().one()
        assert note.title == "URGENT: Campus closed" and "Dr. A. Khan, Managing Director" in note.message and "Heavy rain" in note.message, who
        assert note.link == f"{base}?open={a['id']}", who


async def test_expired_archived_and_pinned_announcements(app_client, admin_user, db_session):
    from datetime import datetime, timedelta, timezone

    _b1, _b2, raw = await _people(app_client, db_session)
    aid = await _authority(app_client)
    await _announce(app_client, aid, title="Older")
    await _announce(app_client, aid, title="Pinned notice", pinned=True)
    await _announce(app_client, aid, title="Expired", expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    gone = await _announce(app_client, aid, title="Withdrawn")
    assert (await app_client.post(f"{API}/{gone['id']}/archive")).json()["status"] == "ARCHIVED"
    await _announce(app_client, aid, title="Newest")
    await _as(app_client, raw, "s_bscs")
    titles = list(await _feed(app_client))
    assert titles == ["Pinned notice", "Newest", "Older"]                      # pinned first, then newest; expired and archived are gone


async def test_reading_is_tracked_and_the_admin_sees_how_far_it_reached(app_client, admin_user, db_session):
    _b1, _b2, raw = await _people(app_client, db_session)
    aid = await _authority(app_client)
    a1, a2 = await _announce(app_client, aid, title="One"), await _announce(app_client, aid, title="Two")
    await _as(app_client, raw, "s_bscs")
    assert (await app_client.get(f"{API}/unread-count")).json() == {"unread_count": 2}
    assert (await app_client.post(f"{API}/{a1['id']}/read")).json() == {"unread_count": 1}
    assert (await app_client.post(f"{API}/{a1['id']}/read")).json() == {"unread_count": 1}              # opening twice counts once
    feed = await _feed(app_client)
    assert feed["One"]["is_read"] is True and feed["Two"]["is_read"] is False
    assert (await app_client.get(API, params={"unread_only": True})).json()[0]["title"] == "Two"
    assert (await app_client.post(f"{API}/read-all")).json() == {"unread_count": 0}
    await _as(app_client, raw, "teacher")
    await app_client.post(f"{API}/{a1['id']}/read")
    await _login(app_client, **ADMIN)
    rows = {r["title"]: r for r in (await app_client.get(f"{API}/manage")).json()}
    assert (rows["One"]["recipients"], rows["One"]["read_count"]) == (5, 2)                                # sent to 5, opened by 2
    assert (rows["Two"]["recipients"], rows["Two"]["read_count"]) == (5, 1)
    await _as(app_client, raw, "s_bscs")
    assert [x["title"] for x in (await app_client.get(API, params={"q": "tw"})).json()] == ["Two"]
    assert [x["title"] for x in (await app_client.get(API, params={"q": "khan"})).json()] == ["Two", "One"] or len((await app_client.get(API, params={"q": "khan"})).json()) == 2


async def test_attachments_accept_real_documents_and_refuse_anything_else(app_client, admin_user, db_session):
    _b1, _b2, raw = await _people(app_client, db_session)
    a = await _announce(app_client, await _authority(app_client), title="Circular")
    up = lambda name, data, ctype: app_client.post(f"{API}/{a['id']}/attachments", files={"file": (name, data, ctype)})        # noqa: E731
    pdf = await up("circular.pdf", PDF, "application/pdf")
    docx = await up("fees.docx", _docx(), DOCX_TYPE)
    png = await up("map.png", b"\x89PNG\r\n\x1a\n" + b"0" * 40, "image/png")
    assert (pdf.status_code, docx.status_code, png.status_code) == (201, 201, 201)
    assert pdf.json()["inline"] is True and docx.json()["inline"] is False and png.json()["is_image"] is True
    # hostile or wrong files
    assert (await up("page.pdf", b"<html><script>alert(1)</script></html>", "application/pdf")).status_code == 422          # claims PDF, is HTML
    assert (await up("fake.docx", _docx(valid=False), DOCX_TYPE)).status_code == 422                                          # a zip that is not a document
    assert (await up("run.exe", b"MZ\x90\x00" + b"0" * 100, "application/octet-stream")).status_code == 422
    assert (await up("notes.txt", b"hello", "text/plain")).status_code == 422
    assert (await up("empty.pdf", b"", "application/pdf")).status_code == 422
    big = await up("huge.pdf", b"%PDF-" + b"0" * (15 * 1024 * 1024 + 10), "application/pdf")
    assert big.status_code == 422 and "too large" in big.text
    for i in range(5):                                                                                                       # 3 so far + 5 = the limit of 8
        assert (await up(f"x{i}.pdf", PDF, "application/pdf")).status_code == 201
    assert (await up("ninth.pdf", PDF, "application/pdf")).status_code == 422
    listed = (await app_client.get(f"{API}/{a['id']}")).json()
    assert len(listed["attachments"]) == 8


async def test_only_the_audience_can_download_and_files_are_served_safely(app_client, admin_user, db_session):
    bscs, _bba, raw = await _people(app_client, db_session)
    aid = await _authority(app_client)
    a = await _announce(app_client, aid, title="Staff memo", audience_students=False, audience_teachers=False)
    pdf = (await app_client.post(f"{API}/{a['id']}/attachments", files={"file": ("memo.pdf", PDF, "application/pdf")})).json()
    docx = (await app_client.post(f"{API}/{a['id']}/attachments", files={"file": ("memo.docx", _docx(), DOCX_TYPE)})).json()
    await _as(app_client, raw, "librarian")
    r = await app_client.get(f"{API}/attachments/{pdf['id']}/file")
    assert r.status_code == 200 and r.content == PDF and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline") and r.headers["x-content-type-options"] == "nosniff"
    d = await app_client.get(f"{API}/attachments/{docx['id']}/file")
    assert d.status_code == 200 and d.headers["content-disposition"].startswith("attachment")                                # Office files always download
    for who in ("s_bscs", "teacher"):
        await _as(app_client, raw, who)
        assert (await app_client.get(f"{API}/attachments/{pdf['id']}/file")).status_code == 403
        assert (await app_client.get(f"{API}/{a['id']}")).status_code == 404
    app_client.cookies.clear()
    assert (await app_client.get(f"{API}/attachments/{pdf['id']}/file")).status_code == 401


async def test_removing_files_and_deleting_an_announcement_cleans_up_the_disk(app_client, admin_user, db_session):
    _b1, _b2, raw = await _people(app_client, db_session)
    a = await _announce(app_client, await _authority(app_client), title="Temporary")
    one = (await app_client.post(f"{API}/{a['id']}/attachments", files={"file": ("a.pdf", PDF, "application/pdf")})).json()
    two = (await app_client.post(f"{API}/{a['id']}/attachments", files={"file": ("b.pdf", PDF, "application/pdf")})).json()
    folder = Path(settings.storage_local_path) / "announcements" / a["id"]
    assert len(list(folder.iterdir())) == 2
    assert (await app_client.delete(f"{API}/attachments/{one['id']}")).status_code == 204
    assert len(list(folder.iterdir())) == 1
    assert (await app_client.delete(f"{API}/{a['id']}")).status_code == 204
    assert not any(folder.iterdir()) if folder.exists() else True
    assert (await app_client.get(f"{API}/{a['id']}")).status_code == 404
    assert (await app_client.get(f"{API}/attachments/{two['id']}/file")).status_code == 404


async def test_everyone_may_see_the_complaint_statistics_but_nothing_that_identifies_anyone(app_client, admin_user, db_session):
    _b1, _b2, raw = await _people(app_client, db_session)
    await _as(app_client, raw, "s_bscs")
    for who in ("s_bscs", "teacher"):
        await _as(app_client, raw, who)
        r = await app_client.get("/api/v1/complaints/stats")
        assert r.status_code == 200, (who, r.text)
        body = r.json()
        assert set(body) == {"kpis", "by_status", "by_reason", "by_week", "resolution_rate"}
        assert "anonymous" not in str(body).lower() and "complainant" not in str(body).lower() and "target" not in str(body).lower()
        assert len(body["by_week"]) == 12
    await _as(app_client, raw, "librarian")
    assert (await app_client.get("/api/v1/complaints/stats")).status_code == 403                  # roles that cannot file complaints have no complaints page
    app_client.cookies.clear()
    assert (await app_client.get("/api/v1/complaints/stats")).status_code == 401