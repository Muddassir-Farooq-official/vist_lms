"""Step 4: Advance Semester holds students back on academic standing and unpaid fines.

Uses a real published result: of three students, one fails the course (25%),
so has 1 course to clear and a CGPA of 0.00.
"""
from __future__ import annotations

import pytest

from app.core.config import settings
from tests.integration.test_attendance_and_grading import _login_admin
from tests.integration.test_result_sheets import _as_teacher, _class, _fill

pytestmark = pytest.mark.asyncio
R = "/api/v1/results"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _published_class(client, db, code):
    offering, students, assessments, teacher = await _class(client, db, code=code, cnic_base=abs(hash(code)) % 9000 + 1000)
    await _as_teacher(client, teacher)
    await _fill(client, students, assessments)          # scores: pass, FAIL (25%), pass
    assert (await client.post(f"{R}/offerings/{offering}/submit")).status_code == 200
    await _login_admin(client)
    assert (await client.post(f"{R}/sheets/{offering}/approve")).status_code == 200
    assert (await client.post(f"{R}/sheets/{offering}/publish")).status_code == 200
    rolls = []
    for sid, _user, _enr in students:
        rolls.append((await client.get(f"/api/v1/students/{sid}")).json()["roll_number"])
    return students, rolls


async def _preview(client, **flags):
    body = {"dry_run": True, "skip_open_enrollments": False, "skip_overdue_fees": False,
            "skip_failed_courses": False, "skip_low_cgpa": False, "skip_unpaid_fines": False, **flags}
    r = await client.post("/api/v1/students/advance-semester", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_failed_courses_hold(app_client, admin_user, db_session, monkeypatch):
    _students, rolls = await _published_class(app_client, db_session, "P4A")
    monkeypatch.setattr(settings, "promotion_max_courses_to_clear", 0)
    r = await _preview(app_client, skip_failed_courses=True)
    held = {s["roll_number"]: s["reason"] for s in r["skipped_students"]}
    assert r["skipped_failed_courses"] == 1 and rolls[1] in held and "failed course" in held[rolls[1]]
    assert rolls[0] not in held and rolls[2] not in held


async def test_low_cgpa_hold(app_client, admin_user, db_session):
    _students, rolls = await _published_class(app_client, db_session, "P4B")
    r = await _preview(app_client, skip_low_cgpa=True)
    held = {s["roll_number"]: s["reason"] for s in r["skipped_students"]}
    assert r["skipped_low_cgpa"] == 1 and "CGPA 0.00" in held[rolls[1]] and r["min_cgpa"] == settings.promotion_min_cgpa


async def test_unpaid_fine_hold_and_clean_students_advance(app_client, admin_user, db_session):
    students, rolls = await _published_class(app_client, db_session, "P4C")
    fine = await app_client.post("/api/v1/fines", json={"payer_type": "STUDENT", "payer_id": students[0][0], "amount": 500,
                                                         "reason": "Damaged lab equipment", "source": "DISCIPLINARY"})
    assert fine.status_code == 201, fine.text
    r = await _preview(app_client, skip_unpaid_fines=True)
    held = {s["roll_number"]: s["reason"] for s in r["skipped_students"]}
    assert r["skipped_unpaid_fines"] == 1 and "unpaid fine" in held[rolls[0]]
    # all holds on (the defaults): the failing student and the fined student are held, the third advances for real
    done = await app_client.post("/api/v1/students/advance-semester", json={"skip_open_enrollments": False})
    assert done.status_code == 200
    held = {s["roll_number"] for s in done.json()["skipped_students"]}
    assert rolls[0] in held and rolls[1] in held and rolls[2] not in held
    before = 1
    after = (await app_client.get(f"/api/v1/students/{students[2][0]}")).json()["current_semester"]
    assert after == before + 1