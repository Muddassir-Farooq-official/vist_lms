"""Weekly timetable grid + PDF: scoping by role, filters, and the printable sheet."""
from __future__ import annotations

import shutil
import subprocess
from datetime import date, timedelta

import pytest
from app.core.clock import institute_today

from tests.integration.test_module3_sections_allocation import (
    _admin,
    _course,
    _login,
    _offering,
    _period,
    _program,
    _slot,
    _teacher,
)
from tests.integration.test_teacher_content_and_bulk_timetable import _sign_in_teacher

pytestmark = pytest.mark.asyncio
TODAY = institute_today()
SPAN = {"start": TODAY - timedelta(days=30), "end": TODAY + timedelta(days=60)}


@pytest.fixture(autouse=True)
def _no_login_throttle(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _employee(client, tid):
    return (await client.get(f"/api/v1/employees/{tid}")).json()


async def _world(client, db):
    """BSCS + BBA, four courses, three teachers, a parallel section B, three periods."""
    await _admin(client)
    bscs, bba = await _program(db, "BSCS"), await _program(db, "BBA")
    p1 = await _period(client, "09:00:00", "10:00:00")
    p2 = await _period(client, "10:00:00", "11:00:00")
    await _period(client, "11:00:00", "12:00:00")            # a period nobody uses: the sheet still shows the row
    t1 = await _teacher(client, db, "TWA", "35202-9000001-1")
    t2 = await _teacher(client, db, "TWB", "35202-9000002-2")
    t3 = await _teacher(client, db, "TWC", "35202-9000003-3")
    c1 = await _course(client, "WK-101", str(bscs.id), 1)
    c2 = await _course(client, "WK-102", str(bscs.id), 1)
    c3 = await _course(client, "WK-301", str(bscs.id), 3)
    c4 = await _course(client, "WK-401", str(bba.id), 1)
    o1 = await _offering(client, c1, teacher=t1, **SPAN)
    o2 = await _offering(client, c2, teacher=t2, **SPAN)
    o3 = await _offering(client, c3, teacher=t1, **SPAN)
    o4 = await _offering(client, c4, teacher=t2, **SPAN)
    o1b = await _offering(client, c1, section="B", teacher=t3, **SPAN)
    for oid, period, room, day in ((o1, p1, "R-1", "MONDAY"), (o2, p2, "R-2", "MONDAY"), (o3, p1, "R-3", "TUESDAY"),
                                   (o4, p1, "R-4", "MONDAY"), (o1b, p1, "R-5", "MONDAY")):
        r = await _slot(client, oid, period, room, day=day)
        assert r.status_code == 201, r.text
    return {"bscs": bscs, "bba": bba, "t1": t1, "t2": t2, "t3": t3, "o": [o1, o2, o3, o4, o1b]}


def _cell(data, day, row_label_start):
    for c in data["cells"]:
        row = next(r for r in data["rows"] if r["key"] == c["row_key"])
        if c["day"] == day and row["label"].startswith(row_label_start):
            return c["entries"]
    return []


def _codes(data):
    return sorted({e["course_code"] + (f"/{e['section_label']}" if e["section_label"] != "A" else "") for c in data["cells"] for e in c["entries"]})


async def _weekly(client, **params):
    r = await client.get("/api/v1/academics/timetable/weekly", params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def test_admin_grid_has_the_sheet_shape(app_client, admin_user, db_session):
    await _world(app_client, db_session)
    data = await _weekly(app_client)
    assert data["scope"] == "admin" and data["term_label"] == "Fall 2026"          # the current term is picked automatically
    assert data["days"] == ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]
    assert [r["label"] for r in data["rows"]] == ["09:00\u201310:00", "10:00\u201311:00", "11:00\u201312:00"]   # unused period still listed
    monday_9 = _cell(data, "MONDAY", "09:00")
    assert {e["course_code"] for e in monday_9} == {"WK-101", "WK-401"} and len(monday_9) == 3      # A + B section + BBA course
    entry = next(e for e in monday_9 if e["course_code"] == "WK-401")
    assert entry["room"] == "R-4" and entry["teacher_name"] and entry["program_codes"] == ["BBA"]
    assert [e["course_code"] for e in _cell(data, "MONDAY", "10:00")] == ["WK-102"]
    assert _cell(data, "TUESDAY", "09:00")[0]["course_code"] == "WK-301"
    assert data["entries_count"] == 5


async def test_admin_can_filter_by_class_semester_section_teacher_and_room(app_client, admin_user, db_session):
    w = await _world(app_client, db_session)
    bscs = str(w["bscs"].id)
    assert _codes(await _weekly(app_client, program_id=bscs)) == ["WK-101", "WK-101/B", "WK-102", "WK-301"]
    sem1 = await _weekly(app_client, program_id=bscs, semester_number=1)
    assert _codes(sem1) == ["WK-101", "WK-101/B", "WK-102"] and sem1["title"] == "Fall 2026 \u00b7 BSCS \u00b7 Semester 1"
    assert _codes(await _weekly(app_client, program_id=bscs, semester_number=3)) == ["WK-301"]
    assert _codes(await _weekly(app_client, semester_number=1)) == ["WK-101", "WK-101/B", "WK-102", "WK-401"]     # semester alone spans classes
    b = await _weekly(app_client, program_id=bscs, semester_number=1, section_label="b")
    assert _codes(b) == ["WK-101/B"] and "Section b" in b["title"]
    assert _codes(await _weekly(app_client, teacher_id=w["t2"])) == ["WK-102", "WK-401"]
    assert _codes(await _weekly(app_client, room="r-3")) == ["WK-301"]
    assert (await _weekly(app_client, program_id=str(w["bba"].id), semester_number=3))["entries_count"] == 0


async def test_weekend_columns_appear_only_when_something_is_scheduled_then(app_client, admin_user, db_session):
    await _world(app_client, db_session)
    period = await _period(app_client, "09:00:00", "10:00:00")
    oid = await _offering(app_client, await _course(app_client, "WK-SAT"), **SPAN)
    assert (await _slot(app_client, oid, period, "R-9", day="SATURDAY")).status_code == 201
    assert (await _weekly(app_client))["days"][-1] == "SATURDAY"
    assert "SATURDAY" in (await _weekly(app_client, room="R-9"))["days"]                  # the column follows the data
    assert "SATURDAY" not in (await _weekly(app_client, room="R-3"))["days"]


async def test_teacher_sees_only_their_own_week_and_cannot_peek_at_others(app_client, admin_user, db_session):
    w = await _world(app_client, db_session)
    emp = await _employee(app_client, w["t1"])
    await _sign_in_teacher(app_client, emp["username"])
    mine = await _weekly(app_client)
    assert mine["scope"] == "teacher" and _codes(mine) == ["WK-101", "WK-301"]
    assert _codes(await _weekly(app_client, teacher_id=w["t2"])) == ["WK-101", "WK-301"]        # someone else's week is not available
    assert _codes(await _weekly(app_client, room="R-4")) == ["WK-101", "WK-301"]              # nor a room search across the institute
    assert _codes(await _weekly(app_client, semester_number=3)) == ["WK-301"]                 # but their own can be narrowed
    opts = (await app_client.get("/api/v1/academics/timetable/filters")).json()
    assert opts["allowed"] == ["term", "program", "semester", "section"] and opts["teachers"] == []
    assert {p["code"] for p in opts["programs"]} == {"BSCS"}


async def test_student_sees_only_the_courses_they_are_enrolled_in(app_client, admin_user, db_session):
    w = await _world(app_client, db_session)
    created = await app_client.post("/api/v1/students", json={
        "first_name": "Sana", "last_name": "Iqbal", "date_of_birth": "2005-03-03", "gender": "Female",
        "cnic": "35202-9100001-1", "contact_no": "03001110000", "marital_status": "Single", "religion": "Islam",
        "nationality": "Pakistani", "guardian_name": "G", "guardian_cnic": "35202-9100002-2",
        "relation_with_guardian": "Father", "emergency_contact_no": "03001110001", "present_address": "x",
        "province": "Punjab", "district": "Lahore", "city": "Lahore",
        "program_id": str(w["bscs"].id), "current_semester": 1})
    assert created.status_code == 201, created.text
    for oid in (w["o"][0], w["o"][1]):                                            # WK-101 (A) and WK-102
        assert (await app_client.post(f"/api/v1/academics/offerings/{oid}/enroll-student", json={"student_id": created.json()["id"]})).status_code == 201
    await _login(app_client, created.json()["username"], "vertex123")
    assert (await app_client.post("/api/v1/auth/change-password", json={"current_password": "vertex123", "new_password": "StudentNew#2026"})).status_code == 204
    data = await _weekly(app_client)
    assert data["scope"] == "student" and _codes(data) == ["WK-101", "WK-102"]      # not WK-101/B, WK-301 or the BBA course
    assert _codes(await _weekly(app_client, teacher_id=w["t3"], room="R-5")) == ["WK-101", "WK-102"]
    opts = (await app_client.get("/api/v1/academics/timetable/filters")).json()
    assert opts["allowed"] == ["term"] and opts["programs"] == [] and [t["label"] for t in opts["terms"]] == ["Fall 2026"]


def _pdf_text(content: bytes) -> str | None:
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    return subprocess.run([exe, "-layout", "-", "-"], input=content, capture_output=True, check=True).stdout.decode("utf-8", "ignore")


async def test_pdf_download_matches_the_grid_and_is_scoped(app_client, admin_user, db_session):
    w = await _world(app_client, db_session)
    r = await app_client.get("/api/v1/academics/timetable/weekly.pdf", params={"program_id": str(w["bscs"].id), "semester_number": 1})
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and len(r.content) > 1500
    assert 'attachment; filename="timetable-fall-2026-bscs-semester-1.pdf"' == r.headers["content-disposition"]
    text = _pdf_text(r.content)
    if text is not None:
        assert "WK-101" in text and "WK-102" in text and "WK-301" not in text and "WK-401" not in text
        assert "MONDAY" in text and "09:00-10:00" in text

    emp = await _employee(app_client, w["t1"])
    await _sign_in_teacher(app_client, emp["username"])
    mine = await app_client.get("/api/v1/academics/timetable/weekly.pdf")
    assert mine.status_code == 200 and mine.content.startswith(b"%PDF")
    mine_text = _pdf_text(mine.content)
    if mine_text is not None:
        assert "WK-301" in mine_text and "WK-102" not in mine_text and "WK-401" not in mine_text


async def test_pdf_for_an_empty_selection_still_produces_a_sheet(app_client, admin_user, db_session):
    w = await _world(app_client, db_session)
    r = await app_client.get("/api/v1/academics/timetable/weekly.pdf", params={"program_id": str(w["bba"].id), "semester_number": 5})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    text = _pdf_text(r.content)
    if text is not None:
        assert "No classes are scheduled" in text


async def test_timetable_needs_a_signed_in_person_with_a_timetable(app_client):
    for path in ("/api/v1/academics/timetable/weekly", "/api/v1/academics/timetable/weekly.pdf", "/api/v1/academics/timetable/filters"):
        assert (await app_client.get(path)).status_code == 401


async def test_semester_filter_says_where_the_classes_actually_are(app_client, admin_user, db_session):
    """A course only appears under the semester it is placed at in the curriculum. When a chosen
    semester is empty, the answer must say which semesters DO have classes."""
    w = await _world(app_client, db_session)
    bscs = str(w["bscs"].id)
    everything = await _weekly(app_client, program_id=bscs)
    assert everything["semester_counts"] == [{"semester": 1, "courses": 3}, {"semester": 3, "courses": 1}]
    empty = await _weekly(app_client, program_id=bscs, semester_number=2)
    assert empty["entries_count"] == 0
    assert empty["semester_counts"] == everything["semester_counts"]              # unchanged by the semester choice itself
    sem3 = await _weekly(app_client, program_id=bscs, semester_number=3)
    assert _codes(sem3) == ["WK-301"] and sem3["entries_count"] == 1
    entry = next(e for c in sem3["cells"] for e in c["entries"])
    assert entry["placements"] == [{"program_code": "BSCS", "semester": 3}]
    # without choosing a class, the counts span every class
    assert {c["semester"]: c["courses"] for c in (await _weekly(app_client))["semester_counts"]} == {1: 4, 3: 1}
    # other filters narrow the counts too (Section B has only the one course)
    assert (await _weekly(app_client, program_id=bscs, section_label="B"))["semester_counts"] == [{"semester": 1, "courses": 1}]