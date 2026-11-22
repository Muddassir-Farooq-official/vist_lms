"""Question bank (app/qbank): teachers add questions only to the courses they teach; the exam office builds a paper
from a blueprint (least-used first; refused, saying what is short, when the bank has too few), swaps, reorders and
makes it final (locked; questions in it can no longer be edited); students and other teachers cannot see it."""
from __future__ import annotations

import io

import pytest

from app.academics.models import CourseOffering
from app.core.config import settings
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_attendance_and_grading import _login, _login_admin
from tests.integration.test_result_sheets import _class

pytestmark = pytest.mark.asyncio
Q = "/api/v1/qbank"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


async def test_teachers_add_exam_office_builds_and_finalises(app_client, admin_user, db_session):
    import uuid

    offering, students, _a, teacher = await _class(app_client, db_session, code="QB1", n=1)
    other_off, _s2, _a2, other = await _class(app_client, db_session, code="QB2", n=1, cnic_base=2100)
    course = str((await db_session.get(CourseOffering, uuid.UUID(offering))).course_id)
    other_course = str((await db_session.get(CourseOffering, uuid.UUID(other_off))).course_id)

    await _login(app_client, teacher, "TeacherNew12345!")
    mine = await _ok(await app_client.get(f"{Q}/courses"))
    assert [c["id"] for c in mine] == [course]
    ids = []
    for i in range(4):
        ids.append((await _ok(await app_client.post(f"{Q}/questions", json={"course_id": course, "kind": "MCQ", "text": f"What is {i}+1?",
                                                                            "options": [str(i), str(i + 1), "x"], "answer": "B", "marks": 1,
                                                                            "difficulty": "EASY", "bloom": "C1", "clo": "clo1"}), 201))["id"])
    for i in range(2):
        await _ok(await app_client.post(f"{Q}/questions", json={"course_id": course, "kind": "LONG", "text": f"Explain topic {i} fully.", "answer": "Key points",
                                                                "marks": 10, "difficulty": "HARD", "bloom": "C4", "clo": "CLO2"}), 201)
    bad = await app_client.post(f"{Q}/questions", json={"course_id": course, "kind": "MCQ", "text": "Pick one please", "options": ["a", "b"], "answer": "C", "marks": 1})
    assert bad.status_code == 422
    assert (await app_client.post(f"{Q}/questions", json={"course_id": other_course, "kind": "SHORT", "text": "Not my course", "marks": 2})).status_code == 403
    bank = await _ok(await app_client.get(f"{Q}/courses/{course}/questions"))
    assert bank["summary"]["MCQ"]["EASY"] == 4 and bank["summary"]["LONG"]["HARD"] == 2
    assert (await app_client.get(f"{Q}/papers")).status_code == 403                         # papers are the exam office's

    # another teacher cannot see or change this course's bank
    await _login(app_client, other, "TeacherNew12345!")
    assert (await app_client.get(f"{Q}/courses/{course}/questions")).status_code == 403
    assert (await app_client.put(f"{Q}/questions/{ids[0]}", json={"text": "Changed by someone else"})).status_code == 403

    # a student has no way in
    await _login(app_client, students[0][1], "StudentPass123!")
    assert (await app_client.get(f"{Q}/courses")).status_code == 403
    assert (await app_client.get(f"{Q}/courses/{course}/questions")).status_code == 403

    # the exam office builds a paper
    await _login_admin(app_client)
    exam, first = await _staff(app_client, db_session, "EXQB", "EXAM_CONTROLLER", "35202-8610001-1")
    await _first_login(app_client, exam, first)
    short = await app_client.post(f"{Q}/papers", json={"course_id": course, "title": "Mid", "blueprint": [{"kind": "LONG", "count": 3}]})
    assert short.status_code == 422 and "the bank has 2" in short.text
    p = await _ok(await app_client.post(f"{Q}/papers", json={"course_id": course, "title": "Midterm", "duration_minutes": 90,
                                                             "blueprint": [{"kind": "MCQ", "count": 3, "difficulty": "EASY"}, {"kind": "LONG", "count": 1, "marks": 12}]}), 201)
    assert [x["kind"] for x in p["items"]] == ["MCQ", "MCQ", "MCQ", "LONG"] and p["total_marks"] == 15.0
    assert p["marks_by_clo"] == {"CLO1": 3.0, "CLO2": 12.0}
    first_mcq = p["items"][0]
    swapped = await _ok(await app_client.post(f"{Q}/papers/{p['id']}/items/{first_mcq['item_id']}/swap"))
    assert swapped["items"][0]["id"] != first_mcq["id"] and swapped["items"][0]["kind"] == "MCQ"
    moved = await _ok(await app_client.post(f"{Q}/papers/{p['id']}/items/{swapped['items'][3]['item_id']}/move", json={"step": -1}))
    assert moved["items"][2]["kind"] == "LONG"
    pdf = await app_client.get(f"{Q}/papers/{p['id']}/pdf")
    key = await app_client.get(f"{Q}/papers/{p['id']}/pdf", params={"key": "true"})
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF" and key.status_code == 200
    from pypdf import PdfReader

    assert "ANSWER KEY" in "".join(pg.extract_text() for pg in PdfReader(io.BytesIO(key.content)).pages)
    assert "ANSWER KEY" not in "".join(pg.extract_text() for pg in PdfReader(io.BytesIO(pdf.content)).pages)

    final = await _ok(await app_client.post(f"{Q}/papers/{p['id']}/final"))
    assert final["status"] == "FINAL"
    assert (await app_client.post(f"{Q}/papers/{p['id']}/items/{final['items'][0]['item_id']}/swap")).status_code == 422
    assert (await app_client.delete(f"{Q}/papers/{p['id']}")).status_code == 422
    used = [x["id"] for x in final["items"]]
    bank = await _ok(await app_client.get(f"{Q}/courses/{course}/questions"))
    assert all(q["times_used"] == (1 if q["id"] in used else 0) for q in bank["questions"])

    # the next paper prefers the unused MCQ
    p2 = await _ok(await app_client.post(f"{Q}/papers", json={"course_id": course, "title": "Quiz", "blueprint": [{"kind": "MCQ", "count": 1}]}), 201)
    assert p2["items"][0]["times_used"] == 0

    # a question in a final paper stays as it was; it can be retired
    await _login(app_client, teacher, "TeacherNew12345!")
    locked = next(q for q in used if q in ids)
    assert (await app_client.put(f"{Q}/questions/{locked}", json={"text": "A different question now"})).status_code == 422
    assert (await app_client.put(f"{Q}/questions/{locked}", json={"is_active": False})).status_code == 200


async def test_pages_render(app_client, admin_user):
    await _login_admin(app_client)
    for path in ("/dashboard/admin/question-papers", "/dashboard/exam/question-papers", "/dashboard/faculty/question-bank"):
        assert (await app_client.get(path)).status_code == 200, path
