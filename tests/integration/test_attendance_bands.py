"""Graded attendance fines per subject (app/attendance/shortfall.py):
75%+ no fine; 65-74% Rs 3,000; 55-64% Rs 6,000; 45-54% Rs 10,000; below 45% an administrator decides."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import settings
from tests.integration.test_attendance_and_grading import _create_n_sessions, _setup_offering_with_enrolled_students

pytestmark = pytest.mark.asyncio
RUN = "/api/v1/attendance/fines/run"
NOTE = "backfilled test data"


@pytest.fixture(autouse=True)
def _rules(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)
    for k, v in {"attendance_fine_mode": 1, "attendance_eligibility_threshold_percent": 75.0, "attendance_band1_from": 65.0,
                 "attendance_band1_amount": 3000.0, "attendance_band2_from": 55.0, "attendance_band2_amount": 6000.0,
                 "attendance_band3_from": 45.0, "attendance_band3_amount": 10000.0}.items():
        monkeypatch.setattr(settings, k, v)


async def _mark(client, session_id, present):
    r = await client.post(f"/api/v1/attendance/sessions/{session_id}/mark", json={"present_student_ids": list(present), "late_note": NOTE})
    assert r.status_code == 200, r.text


async def _class(client, code, attended: list[int], n=10, cnic_base=6100, extra=0):
    """One course with a student per entry of `attended` (classes attended out of the first n). `extra` more classes
    are created but left unmarked (returned as the third value)."""
    offering_id, studs = await _setup_offering_with_enrolled_students(client, code=code, n_students=len(attended), cnic_base=cnic_base)
    sess = await _create_n_sessions(client, offering_id, n + extra)
    ids = [s[0] for s in studs]
    for i, sid in enumerate(sess[:n]):
        await _mark(client, sid, [ids[k] for k, a in enumerate(attended) if i < a])
    return offering_id, ids, sess[n:]


async def _fines(db_session, student_id):
    from app.fines.models import Fine

    return (await db_session.execute(select(Fine).where(Fine.payer_id == student_id).order_by(Fine.issued_at)
                                      .execution_options(populate_existing=True))).scalars().all()


async def test_fines_by_band_per_subject_preview_then_charge_once(app_client, admin_user, db_session):
    offering_id, (s80, s70, s60, s50, s40), _ = await _class(app_client, "BND1", [8, 7, 6, 5, 4])
    pv = await app_client.post(RUN, json={"term_label": "Fall 2026"})
    assert pv.status_code == 200, pv.text
    p = pv.json()
    assert p["dry_run"] and p["new_fines"] == 3 and p["to_review"] == 1 and p["amount"] == 19000.0
    by = {i["student_id"]: i for i in p["items"]}
    assert s80 not in by
    assert (by[s70]["band"], by[s70]["charge"]) == (1, 3000.0)
    assert (by[s60]["band"], by[s60]["charge"]) == (2, 6000.0)
    assert (by[s50]["band"], by[s50]["charge"]) == (3, 10000.0)
    assert by[s40]["action"] == "review" and by[s40]["charge"] == 0.0
    assert await _fines(db_session, s70) == []                                     # a preview charges nothing

    r = await app_client.post(RUN, json={"term_label": "Fall 2026", "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["new_fines"] == 3 and r.json()["amount"] == 19000.0
    assert [float(f.amount) for f in await _fines(db_session, s60)] == [6000.0]
    assert await _fines(db_session, s40) == []                                     # below 45%: no automatic fine or drop
    again = (await app_client.post(RUN, json={"term_label": "Fall 2026", "confirm": True})).json()
    assert again["new_fines"] == 0 and again["amount"] == 0.0                       # never charged twice
    listed = {x["student_id"]: x for x in (await app_client.get("/api/v1/attendance/shortfalls", params={"term_label": "Fall 2026"})).json()}
    assert listed[s40]["status"] == "AWAITING_DECISION" and listed[s70]["status"] == "FINED" and listed[s70]["fines_unpaid"] == 3000.0


async def test_falling_into_a_worse_band_charges_only_the_difference(app_client, admin_user, db_session):
    offering_id, (s,), later = await _class(app_client, "BND2", [5], n=7, cnic_base=6200, extra=2)          # 5 of 7 = 71.43%
    assert (await app_client.post(RUN, json={"term_label": "Fall 2026", "confirm": True})).json()["amount"] == 3000.0
    for sid in later:
        await _mark(app_client, sid, [])                                           # absent: 5 of 9 = 55.56% -> band 2
    r = (await app_client.post(RUN, json={"term_label": "Fall 2026", "confirm": True})).json()
    assert r["top_ups"] == 1 and r["amount"] == 3000.0
    assert [float(f.amount) for f in await _fines(db_session, s)] == [3000.0, 3000.0]


async def test_paying_the_fine_lets_marks_decide(app_client, admin_user, db_session):
    from app.attendance.shortfall import cleared
    from app.fines.models import FineStatus

    offering_id, (s,), _ = await _class(app_client, "BND3", [6], cnic_base=6300)
    import uuid

    oid, sid = uuid.UUID(offering_id), uuid.UUID(s)
    assert await cleared(db_session, sid, oid, 60.0) is False                     # not charged yet
    await app_client.post(RUN, json={"term_label": "Fall 2026", "confirm": True})
    assert await cleared(db_session, sid, oid, 60.0) is False                     # charged, not paid
    (fine,) = await _fines(db_session, s)
    fine.status = FineStatus.PAID
    await db_session.commit()
    assert await cleared(db_session, sid, oid, 60.0) is True
    assert await cleared(db_session, sid, oid, 50.0) is False                     # a worse band than paid for: the difference first
    settings.attendance_fine_mode = 0
    assert await cleared(db_session, sid, oid, 60.0) is False                     # the older rule never lifts it


async def test_below_the_lowest_band_the_administration_decides(app_client, admin_user, db_session):
    from app.academics.models import Enrollment

    offering_id, (a, b, c), _ = await _class(app_client, "BND4", [4, 3, 2], cnic_base=6400)
    await app_client.post(RUN, json={"term_label": "Fall 2026", "confirm": True})
    rows = {x["student_id"]: x for x in (await app_client.get("/api/v1/attendance/shortfalls", params={"status": "AWAITING_DECISION"})).json()}
    assert set(rows) >= {a, b, c}
    url = lambda sid: f"/api/v1/attendance/shortfalls/{rows[sid]['id']}/decide"
    assert (await app_client.post(url(a), json={"decision": "FINE", "note": "ok"})).status_code == 422          # reason too short
    r = await app_client.post(url(a), json={"decision": "FINE", "note": "Medical history, allowed by principal"})
    assert r.status_code == 200 and r.json()["status"] == "ALLOWED_WITH_FINE" and r.json()["amount_charged"] == 10000.0
    r = await app_client.post(url(b), json={"decision": "EXCUSE", "note": "Hospitalised, certificate seen"})
    assert r.json()["status"] == "EXCUSED"
    r = await app_client.post(url(c), json={"decision": "DROP", "note": "Never attended after week 2"})
    assert r.json()["status"] == "DROPPED"
    enr = (await db_session.execute(select(Enrollment).where(Enrollment.student_id == c).execution_options(populate_existing=True))).scalar_one()
    assert enr.status.value == "WITHDRAWN"
    assert (await app_client.post(url(b), json={"decision": "DROP", "note": "changed my mind"})).status_code == 422   # decided once


async def test_locking_runs_the_same_check_without_dropping_anyone(app_client, admin_user, db_session):
    offering_id, (s70, s40), _ = await _class(app_client, "BND5", [7, 4], cnic_base=6500)
    pv = (await app_client.get(f"/api/v1/attendance/offerings/{offering_id}/lock-preview")).json()
    assert pv["graded_fines"] and pv["would_fine"] == 1 and pv["would_review"] == 1 and pv["would_drop"] == 0
    r = await app_client.post(f"/api/v1/attendance/offerings/{offering_id}/lock", json={"early_reason": "Term finished early for tests"})
    assert r.status_code == 200, r.text
    assert r.json()["fined_count"] == 1 and r.json()["to_review_count"] == 1 and r.json()["dropped_count"] == 0
    assert [float(f.amount) for f in await _fines(db_session, s70)] == [3000.0]


async def test_weekly_warning_names_the_subject_and_the_fine(app_client, admin_user, db_session, monkeypatch):
    from app.attendance.shortfall import weekly_warnings
    from app.notifications.models import Notification
    from app.students.models import Student

    monkeypatch.setattr(settings, "attendance_warning_min_classes", 5)
    offering_id, (ok, low), _ = await _class(app_client, "BND6", [9, 6], cnic_base=6600)
    assert await weekly_warnings(db_session, force=True) == 1
    await db_session.commit()
    st = await db_session.get(Student, __import__("uuid").UUID(low))
    msgs = (await db_session.execute(select(Notification).where(Notification.user_id == st.user_id, Notification.title == "Attendance warning"))).scalars().all()
    assert len(msgs) == 1 and "BND6" in msgs[0].message and "Rs. 6,000" in msgs[0].message
    await app_client.post(RUN, json={"term_label": "Fall 2026", "confirm": True})
    assert await weekly_warnings(db_session, force=True) == 0                     # already fined at this band: no more warnings


async def test_the_older_rule_keeps_the_run_switched_off(app_client, admin_user, db_session, monkeypatch):
    from tests.integration.test_attendance_and_grading import _login_admin

    await _login_admin(app_client)
    monkeypatch.setattr(settings, "attendance_fine_mode", 0)
    r = await app_client.post(RUN, json={"term_label": "Fall 2026"})
    assert r.status_code == 422 and "older rule" in r.text


def test_classes_needed_to_reach_a_level_and_classes_that_can_still_be_missed():
    from app.attendance.shortfall import classes_can_miss, classes_to_reach, pct_of

    assert classes_to_reach(6, 10, 65) == 2                     # 8 of 12 = 66.7%; 7 of 11 = 63.6% is not enough
    assert classes_to_reach(6, 10, 75) == 6                     # 12 of 16 = 75%
    assert classes_to_reach(8, 10, 75) == 0
    for p, t, target in [(3, 10, 45), (13, 20, 75), (1, 7, 55), (44, 60, 75)]:
        n = classes_to_reach(p, t, target)
        assert pct_of(p + n, t + n) >= target and (n == 0 or pct_of(p + n - 1, t + n - 1) < target)
    assert classes_can_miss(9, 10, 75) == 2                      # 9 of 12 = 75%; 9 of 13 is below
    assert classes_can_miss(3, 4, 75) == 0


async def test_a_student_sees_the_fine_and_the_classes_that_bring_it_down(app_client, admin_user, db_session):
    from tests.integration.test_attendance_and_grading import _login, _login_admin

    offering_id, ids, _ = await _class(app_client, "BND7", [6, 9], cnic_base=6700)
    from app.students.models import Student

    st = await db_session.get(Student, __import__("uuid").UUID(ids[0]))
    from app.auth.models import User

    user = await db_session.get(User, st.user_id)
    await _login(app_client, user.username, "StudentPass123!")
    (r,) = (await app_client.get("/api/v1/attendance/me/risk")).json()
    assert r["course_code"] == "BND7-C1" and r["percentage"] == 60.0 and r["band"] == 2 and r["fine"] == 6000.0
    assert r["next"]["to"] == 65.0 and r["next"]["classes"] == 2 and r["next"]["label"] == "a fine of Rs. 3,000"
    assert r["message"].startswith("60%: the fine is Rs. 6,000.")
    await _login_admin(app_client)
    await app_client.post(RUN, json={"term_label": "Fall 2026", "confirm": True})
    assert (await app_client.get("/api/v1/attendance/shortfalls/count")).json() == {"waiting": 0}
    await _login(app_client, user.username, "StudentPass123!")
    (r,) = (await app_client.get("/api/v1/attendance/me/risk")).json()
    assert r["charged"] == {"amount": 6000.0, "unpaid": 6000.0} and "has been charged" in r["message"]


async def test_the_dashboard_counts_students_waiting_for_a_decision(app_client, admin_user, db_session):
    from tests.integration.test_attendance_and_grading import _login_admin

    await _class(app_client, "BND8", [4, 3], cnic_base=6800)
    await _login_admin(app_client)
    await app_client.post(RUN, json={"term_label": "Fall 2026", "confirm": True})
    assert (await app_client.get("/api/v1/attendance/shortfalls/count")).json() == {"waiting": 2}
