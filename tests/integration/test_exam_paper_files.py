"""Exam paper files (app/paperfiles): the course's teacher sends the question paper (PDF or Word, real file bytes only);
the exam office downloads, marks printed or returns it with a note (the teacher is told); a printed paper can no longer
be replaced by the teacher; another teacher and students cannot see or send it; every version is kept."""
from __future__ import annotations

import io
import zipfile
from datetime import date

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.notifications.models import Notification
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _login, _login_admin
from tests.integration.test_result_sheets import _class

pytestmark = pytest.mark.asyncio
F = "/api/v1/exam-paper-files"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


async def test_teacher_sends_exam_office_prints_or_returns(app_client, admin_user, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_local_path", str(tmp_path))
    offering, students, _a, teacher = await _class(app_client, db_session, code="EPF1", n=1)
    _off2, _s2, _a2, other = await _class(app_client, db_session, code="EPF2", n=1, cnic_base=2300)

    await _login_admin(app_client)
    sched = (await _ok(await app_client.post("/api/v1/exams/schedules", json={"term_label": "Fall 2026", "name": "Midterm"}), 201))["id"]
    day = "2026-03-05"                                       # inside the test term; "today" is moved to ten days before it
    paper = (await _ok(await app_client.post(f"/api/v1/exams/schedules/{sched}/papers", json={"course_offering_id": offering, "exam_date": day,
                                                                                               "start_time": "09:00", "end_time": "11:00", "room": "Hall"}), 201))["papers"][0]["id"]

    import app.core.clock as clock

    monkeypatch.setattr(clock, "institute_today", lambda: date(2026, 2, 23))

    # the course's teacher sees the paper and sends it
    await _login(app_client, teacher, "TeacherNew12345!")
    mine = await _ok(await app_client.get(f"{F}/mine"))
    assert [p["paper_id"] for p in mine] == [paper] and mine[0]["file"] is None and mine[0]["draft"]
    bad = await app_client.post(f"{F}/papers/{paper}", files={"file": ("paper.pdf", b"not really a pdf", "application/pdf")})
    assert bad.status_code in (415, 422)
    img = await app_client.post(f"{F}/papers/{paper}", files={"file": ("p.png", b"\x89PNG\r\n\x1a\n" + b"0" * 20, "image/png")})
    assert img.status_code == 422                                                              # PDF or Word only
    v1 = await _ok(await app_client.post(f"{F}/papers/{paper}", files={"file": ("Mid.pdf", PDF, "application/pdf")}, data={"note": "60 copies"}), 201)
    assert v1["version"] == 1 and v1["status"] == "SENT" and v1["note"] == "60 copies"
    v2 = await _ok(await app_client.post(f"{F}/papers/{paper}", files={"file": ("Mid v2.docx", _docx(),
                                                                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}), 201)
    assert v2["version"] == 2
    hist = await _ok(await app_client.get(f"{F}/papers/{paper}/history"))
    assert [(h["version"], h["status"]) for h in hist] == [(2, "SENT"), (1, "REPLACED")]
    assert (await app_client.get(f"{F}/files/{v1['id']}")).content == PDF                       # every version kept
    assert (await app_client.get(f"{F}/schedules/{sched}")).status_code == 403                 # the overview is the exam office's

    # another teacher and a student cannot see or send it
    await _login(app_client, other, "TeacherNew12345!")
    assert await _ok(await app_client.get(f"{F}/mine")) == []
    assert (await app_client.get(f"{F}/files/{v2['id']}")).status_code == 403
    assert (await app_client.post(f"{F}/papers/{paper}", files={"file": ("x.pdf", PDF, "application/pdf")})).status_code == 403
    await _login(app_client, students[0][1], "StudentPass123!")
    assert (await app_client.get(f"{F}/files/{v2['id']}")).status_code == 403
    assert (await app_client.get(f"{F}/mine")).status_code == 403

    # the exam office returns it, the teacher corrects it, the exam office prints it
    await _login_admin(app_client)
    exam, first = await _staff(app_client, db_session, "EXPF", "EXAM_CONTROLLER", "35202-8710001-1")
    exam_pw = await _first_login(app_client, exam, first)
    ov = await _ok(await app_client.get(f"{F}/schedules/{sched}"))
    assert ov["count"]["SENT"] == 1 and ov["papers"][0]["file"]["version"] == 2
    dl = await app_client.get(f"{F}/files/{v2['id']}")
    assert dl.status_code == 200 and dl.headers["cache-control"] == "no-store"
    assert (await app_client.post(f"{F}/files/{v2['id']}/decide", json={"printed": False})).status_code == 422       # needs a reason
    await _ok(await app_client.post(f"{F}/files/{v2['id']}/decide", json={"printed": False, "note": "Q3 marks do not add up"}))
    assert (await app_client.post(f"{F}/files/{v1['id']}/decide", json={"printed": True})).status_code == 422       # an old version

    await _login(app_client, teacher, "TeacherNew12345!")
    mine = await _ok(await app_client.get(f"{F}/mine"))
    assert mine[0]["file"]["status"] == "RETURNED" and mine[0]["file"]["return_note"] == "Q3 marks do not add up"
    v3 = await _ok(await app_client.post(f"{F}/papers/{paper}", files={"file": ("Mid v3.pdf", PDF, "application/pdf")}), 201)

    await _login(app_client, exam, exam_pw)
    await _ok(await app_client.post(f"{F}/files/{v3['id']}/decide", json={"printed": True}))

    await _login(app_client, teacher, "TeacherNew12345!")
    blocked = await app_client.post(f"{F}/papers/{paper}", files={"file": ("late.pdf", PDF, "application/pdf")})
    assert blocked.status_code == 422 and "already printed" in blocked.text
    db_session.expire_all()
    titles = (await db_session.execute(select(Notification.title).where(Notification.title.like("Your % paper was %")))).scalars().all()
    assert any("returned" in t for t in titles) and any("printed" in t for t in titles)


async def test_pages_render(app_client, admin_user):
    await _login_admin(app_client)
    for path in ("/dashboard/admin/paper-files", "/dashboard/exam/paper-files", "/dashboard/faculty/exam-papers"):
        assert (await app_client.get(path)).status_code == 200, path
