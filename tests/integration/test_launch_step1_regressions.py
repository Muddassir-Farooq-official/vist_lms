"""Step 1 (P0) launch fixes: each test pins a bug found in the live review.

- fee account + scholarships crashed: FeeStatusCalculator.installments() was missing
- the student "My Fees" page was an empty file
- the admin End Semester button called a preview endpoint that did not exist
- End Semester silently "succeeded" for a misspelled term
"""
from __future__ import annotations

import pytest

from tests.integration.test_fees import (
    _create_active_fee_structure,
    _create_and_activate_student,
    _login,
    _login_admin,
)

pytestmark = pytest.mark.asyncio


async def test_student_fee_account_lists_installments_with_challans(app_client, admin_user):
    _sid, user = await _create_and_activate_student(app_client, cnic="71001-0000001-1")
    await _create_active_fee_structure(app_client, total_amount=8000, installment_count=2)
    await _login(app_client, user, "StudentPass123!")
    r = await app_client.get("/api/v1/fees/accounts/me")
    assert r.status_code == 200, r.text
    body = r.json()
    rows = body["assignments"][0]["installments"]
    assert [x["charged"] for x in rows] == [4000.0, 4000.0]
    assert all(x["challan"] and x["challan"]["id"] for x in rows)
    assert body["summary"]["charged"] == 8000.0 and body["summary"]["remaining"] == 8000.0


async def test_staff_can_open_a_students_fee_account(app_client, admin_user):
    sid, _user = await _create_and_activate_student(app_client, cnic="71001-0000002-2")
    await _create_active_fee_structure(app_client, total_amount=5000, installment_count=1)
    await _login_admin(app_client)
    r = await app_client.get(f"/api/v1/fees/accounts/students/{sid}")
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["remaining"] == 5000.0


async def test_percent_scholarship_halves_each_installment_and_its_challan(app_client, admin_user):
    sid, user = await _create_and_activate_student(app_client, cnic="71001-0000003-3")
    await _create_active_fee_structure(app_client, total_amount=10000, installment_count=2)
    await _login_admin(app_client)
    r = await app_client.post("/api/v1/fees/scholarships", json={
        "student_id": sid, "name": "Merit 50%", "kind": "PERCENT", "value": 50, "reason": "Top of merit list"})
    assert r.status_code == 201, r.text
    await _login(app_client, user, "StudentPass123!")
    acct = (await app_client.get("/api/v1/fees/accounts/me")).json()
    rows = acct["assignments"][0]["installments"]
    assert [(x["concession"], x["remaining"]) for x in rows] == [(2500.0, 2500.0), (2500.0, 2500.0)]
    assert [x["challan"]["amount"] for x in rows] == [2500.0, 2500.0]


async def test_student_fees_page_is_not_blank(app_client, admin_user):
    _sid, user = await _create_and_activate_student(app_client, cnic="71001-0000004-4")
    await _login(app_client, user, "StudentPass123!")
    page = await app_client.get("/dashboard/student/fees")
    assert page.status_code == 200
    assert "student-fees.js" in page.text and "My Fees" in page.text
    assert (await app_client.get("/static/js/student-fees.js")).status_code == 200


async def test_end_semester_preview_and_term_typo_is_refused(app_client, admin_user):
    await _login_admin(app_client)
    course = (await app_client.post("/api/v1/academics/courses", json={"code": "ES101", "title": "Prev", "credit_hours": 3})).json()
    r = await app_client.post("/api/v1/academics/offerings", json={"course_id": course["id"], "term_label": "Spring 2027"})
    assert r.status_code == 201, r.text
    p = await app_client.get("/api/v1/academics/end-semester/preview", params={"term_label": "Spring 2027"})
    assert p.status_code == 200, p.text
    assert p.json()["offerings_affected"] == 1 and p.json()["enrollments_to_complete"] == 0
    typo = await app_client.post("/api/v1/academics/end-semester", params={"term_label": "spring 2027"})
    assert typo.status_code == 404 and "Spring 2027" in typo.json()["error"]["message"]