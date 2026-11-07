"""Step 5: institute policy is editable by an administrator, validated, audited, and actually used."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.audit.models import AuditLog
from app.core.config import settings
from tests.integration.test_attendance_and_grading import (
    _cancel_unmarked_sessions,
    _create_n_sessions,
    _login,
    _login_admin,
    _setup_offering_with_enrolled_students,
)
from tests.integration.test_exam_controller import _exam_user

pytestmark = pytest.mark.asyncio
P = "/api/v1/policy"


async def test_admin_reads_and_changes_policy_and_it_is_audited(app_client, admin_user, db_session):
    await _login_admin(app_client)
    items = {p["key"]: p for p in (await app_client.get(P)).json()}
    assert items["attendance_fine_amount"]["value"] == settings.attendance_fine_amount and items["attendance_fine_amount"]["overridden"] is False
    r = await app_client.put(P, json={"values": {"attendance_fine_amount": 2500, "promotion_min_cgpa": 2.0}})
    assert r.status_code == 200, r.text
    after = {p["key"]: p for p in r.json()}
    assert after["attendance_fine_amount"]["value"] == 2500 and after["attendance_fine_amount"]["overridden"] is True
    assert settings.attendance_fine_amount == 2500 and settings.promotion_min_cgpa == 2.0
    cfg = (await app_client.get("/api/v1/attendance/config")).json()
    assert cfg["fine_threshold"] == settings.attendance_fine_threshold_percent
    log = (await db_session.execute(select(AuditLog).where(AuditLog.action == "policy.updated"))).scalars().all()
    assert len(log) == 1
    # null puts it back to the default
    back = {p["key"]: p for p in (await app_client.put(P, json={"values": {"attendance_fine_amount": None}})).json()}
    assert back["attendance_fine_amount"]["overridden"] is False and back["attendance_fine_amount"]["value"] == back["attendance_fine_amount"]["default"]


async def test_bad_values_are_refused(app_client, admin_user):
    await _login_admin(app_client)
    out_of_range = await app_client.put(P, json={"values": {"promotion_min_cgpa": 5}})
    assert out_of_range.status_code == 422 and "between 0 and 4" in out_of_range.text
    not_whole = await app_client.put(P, json={"values": {"attendance_marking_window_minutes": 12.5}})
    assert not_whole.status_code == 422 and "whole number" in not_whole.text
    inconsistent = await app_client.put(P, json={"values": {"attendance_drop_threshold_percent": 70}})
    assert inconsistent.status_code == 422 and "drop" in inconsistent.text
    unknown = await app_client.put(P, json={"values": {"secret_key": 1}})
    assert unknown.status_code == 422


async def test_only_admin_can_see_or_change_policy(app_client, admin_user, db_session):
    user, pw = await _exam_user(app_client, db_session)
    await _login(app_client, user, pw)
    assert (await app_client.get(P)).status_code == 403
    assert (await app_client.put(P, json={"values": {"attendance_fine_amount": 1}})).status_code == 403


async def test_changed_fine_amount_is_what_attendance_lock_charges(app_client, admin_user, db_session):
    await _login_admin(app_client)
    # the older rule (one fine per term at lock); fines by band have their own amounts per band
    assert (await app_client.put(P, json={"values": {"attendance_fine_amount": 1750, "attendance_fine_mode": 0}})).status_code == 200
    offering_id, students = await _setup_offering_with_enrolled_students(app_client, code="P5A", n_students=1, cnic_base=6300)
    student_id = students[0][0]
    for i, sid in enumerate(await _create_n_sessions(app_client, offering_id, 5)):   # 60% -> fined
        await app_client.post(f"/api/v1/attendance/sessions/{sid}/mark", json={"present_student_ids": [student_id] if i < 3 else [], "late_note": "test"})
    await _cancel_unmarked_sessions(app_client, offering_id)
    assert (await app_client.post(f"/api/v1/attendance/offerings/{offering_id}/lock")).json()["fined_count"] == 1
    fine = next(f for f in (await app_client.get("/api/v1/fines")).json() if f["payer_id"] == student_id)
    assert fine["amount"] == 1750