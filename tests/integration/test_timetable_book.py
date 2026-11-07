"""The whole term in one PDF: one timetable per class (program, semester, section) or per teacher; a teacher's sheet
shows their sections and credit hours."""
from __future__ import annotations

import io

import pytest
from pypdf import PdfReader

from tests.integration.test_weekly_timetable import _employee, _no_login_throttle, _weekly, _world  # noqa: F401

pytestmark = pytest.mark.asyncio
BOOK = "/api/v1/academics/timetable/book.pdf"


def _pages(content: bytes) -> list[str]:
    return [p.extract_text() or "" for p in PdfReader(io.BytesIO(content)).pages]


async def test_every_class_gets_its_own_page(app_client, admin_user, db_session):
    await _world(app_client, db_session)
    r = await app_client.get(BOOK, params={"by": "class"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf", r.text
    assert 'filename="timetables-classes-fall-2026.pdf"' in r.headers["content-disposition"]
    pages = _pages(r.content)
    # BBA sem 1 (A), BSCS sem 1 (A), BSCS sem 1 (B), BSCS sem 3 (A)
    assert len(pages) == 4
    assert "BBA" in pages[0] and "WK-401" in pages[0]
    assert "Section B" in pages[2] and "WK-101" in pages[2] and "WK-102" not in pages[2]


async def test_every_teacher_gets_a_page_with_their_credit_hours(app_client, admin_user, db_session):
    w = await _world(app_client, db_session)
    r = await app_client.get(BOOK, params={"by": "teacher"})
    assert r.status_code == 200, r.text
    pages = _pages(r.content)
    assert len(pages) == 3
    t1 = await _employee(app_client, w["t1"])
    page = next(p for p in pages if t1["first_name"] in p)
    assert "Teaching 2 course section(s)" in page and "WK-101" in page and "WK-301" in page
    data = await _weekly(app_client, teacher_id=w["t1"])
    assert data["note"].startswith("Teaching 2 course section(s), ") and "credit hours in Fall 2026" in data["note"]


async def test_only_the_administration_can_print_the_whole_term(app_client, admin_user, db_session):
    from tests.integration.test_teacher_content_and_bulk_timetable import _sign_in_teacher

    w = await _world(app_client, db_session)
    assert (await app_client.get(BOOK, params={"by": "nobody"})).status_code == 422
    emp = await _employee(app_client, w["t2"])
    await _sign_in_teacher(app_client, emp["username"])
    assert (await app_client.get(BOOK, params={"by": "class"})).status_code == 403
