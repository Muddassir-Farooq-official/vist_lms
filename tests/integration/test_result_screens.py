"""The result-approval screens exist and are wired to the workflow."""
from __future__ import annotations

import pytest

from app.main import ADMIN_NAV_ITEMS

pytestmark = pytest.mark.asyncio


async def test_the_admin_has_a_result_approval_page_with_four_lists(app_client):
    entry = next(i for i in ADMIN_NAV_ITEMS if i["slug"] == "result-sheets")
    assert entry["href"] == "/dashboard/admin/result-sheets" and entry["group"] == "Academics"
    html = (await app_client.get(entry["href"])).text
    for tab in ("SUBMITTED", "APPROVED", "PUBLISHED", "RETURNED"):
        assert f'data-tab="{tab}"' in html, tab
    for needle in ('id="rs-publish-all"', 'id="rs-modal"', 'id="rs-act"', 'id="rs-projection"', "result-sheets-admin.js"):
        assert needle in html, needle
    js = (await app_client.get("/static/js/result-sheets-admin.js")).text
    for needle in ("/publish-approved", 'act("approve"', "/return", "correction_reason", "Correct a published mark", "Publish to students"):
        assert needle in js, needle


async def test_the_teacher_page_shows_the_approval_state_and_locks_when_submitted(app_client):
    html = (await app_client.get("/dashboard/faculty/grading")).text
    assert 'id="sheet-status"' in html and "draft" in html.lower() and "approved and published" in html
    js = (await app_client.get("/static/js/faculty-grading.js")).text
    for needle in ("/api/v1/results/offerings/", "Submit for approval", "Sent back for correction", "applyStructureLock", "sheet.can_edit"):
        assert needle in js, needle


async def test_the_student_page_explains_unpublished_results(app_client):
    js = (await app_client.get("/static/js/student-results.js")).text
    assert "grade appears when the result is published" in js and "No marks have been entered" in js
    assert "published" in (await app_client.get("/dashboard/student/results")).text.lower()
    assert "RESULT:" in (await app_client.get("/static/js/admin-common.js")).text


async def test_the_admin_can_edit_the_grading_scale_and_open_result_cards(app_client):
    html = (await app_client.get("/dashboard/admin/result-sheets")).text
    for needle in ('data-tab="SCALE"', 'data-tab="CARDS"', 'id="gs-body"', 'id="gs-save"', 'id="gs-reset"', 'id="rc-q"', 'id="rc-modal"', 'id="rc-pdf"', "Changing the scale does not change results that are already published", "Start again from the standard scale"):
        assert needle in html, needle
    js = (await app_client.get("/static/js/result-sheets-admin.js")).text
    for needle in ("/scale", "/transcript", "transcript.pdf", "rs-dist", "<th>Grade</th>"):
        assert needle in js, needle


async def test_the_student_sees_grades_cgpa_and_can_download_the_result_card(app_client):
    html = (await app_client.get("/dashboard/student/results")).text
    for needle in ('id="results-summary"', 'id="rsum-download"', "/api/v1/results/me/transcript.pdf", "data-download"):
        assert needle in html, needle
    js = (await app_client.get("/static/js/student-results.js")).text
    for needle in ("/api/v1/results/me/transcript", "grade-badge", "Term GPA", "CGPA after this term", "failed: attendance below the limit", "result-course"):
        assert needle in js, needle