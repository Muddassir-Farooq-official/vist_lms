"""Hostel mess, night roll call, transport and the monthly charge drafts (app/hostel, app/transport, app/monthly_charges):
the offices prepare a month's draft, nothing reaches a student's dues until the accounts office approves it, and an
approved month is never charged twice; an absent resident's parents are told once per night."""
from __future__ import annotations

import calendar
import uuid

import pytest
from sqlalchemy import select

from app.core.clock import institute_today
from app.core.config import settings
from app.fines.models import Fine, FineSource
from app.notifications.models import Notification
from app.students.models import Student
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_fees import _create_accounts_officer, _create_and_activate_student, _login, _login_admin
from tests.integration.test_parent_portal import _parent_login

pytestmark = pytest.mark.asyncio
H, T, M = "/api/v1/hostel", "/api/v1/transport", "/api/v1/monthly-charges"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


async def _fines(db, sid, source):
    db.expire_all()
    return (await db.execute(select(Fine).where(Fine.payer_id == uuid.UUID(str(sid)), Fine.source == source))).scalars().all()


async def test_hostel_draft_mess_roll_call_and_accounts_approval(app_client, admin_user, db_session, monkeypatch):
    monkeypatch.setattr(settings, "hostel_mess_fee", 6000.0)
    a, _ua = await _create_and_activate_student(app_client, cnic="76201-0000001-1")
    b, _ub = await _create_and_activate_student(app_client, cnic="76201-0000002-2")
    await _login_admin(app_client)
    made = await _ok(await app_client.post(f"/api/v1/parents/students/{a}"))
    month = institute_today().replace(day=1)
    room = await _ok(await app_client.post(f"{H}/rooms", json={"code": "M1", "capacity": 3, "monthly_fee": 10000}), 201)
    sa = await _ok(await app_client.post(f"{H}/stays", json={"room_id": room["id"], "student_id": a, "start_date": str(month)}), 201)
    await _ok(await app_client.post(f"{H}/stays", json={"room_id": room["id"], "student_id": b, "start_date": str(month), "mess_fee": 0}), 201)
    assert (await _ok(await app_client.post(f"{H}/stays/{sa['id']}/mess", json={"mess_fee": 5000})))["mess_fee"] == 5000.0

    # the warden prepares the draft; the warden cannot charge directly
    await _login_admin(app_client)
    warden, first = await _staff(app_client, db_session, "WRDN", "HOSTEL_WARDEN", "35202-7620001-1")
    await _first_login(app_client, warden, first)
    assert (await app_client.post(f"{H}/charges", json={"month": str(month)})).status_code == 403
    assert (await app_client.post(f"{H}/charges", json={"month": str(month), "dry_run": True})).status_code == 200
    assert (await app_client.post(f"{M}/prepare", json={"kind": "TRANSPORT", "month": str(month)})).status_code == 403
    d1 = await _ok(await app_client.post(f"{M}/prepare", json={"kind": "HOSTEL", "month": str(month)}))
    assert d1["status"] == "DRAFT" and d1["students"] == 2 and d1["total"] == 25000.0
    assert sorted((ln["kind"], ln["amount"]) for ln in d1["lines"]) == [("HOSTEL", 10000.0), ("HOSTEL", 10000.0), ("MESS", 5000.0)]
    assert await _fines(db_session, a, FineSource.HOSTEL) == []                 # nothing charged yet
    d2 = await _ok(await app_client.post(f"{M}/prepare", json={"kind": "HOSTEL", "month": str(month)}))     # prepared again: replaces it
    assert (await _ok(await app_client.get(f"{M}/{d1['id']}")))["status"] == "DISCARDED"
    assert (await app_client.post(f"{M}/{d2['id']}/approve")).status_code == 403    # the warden cannot approve

    # night roll call: an absent resident's parent and the student are told, once
    today = institute_today()
    rc = await _ok(await app_client.get(f"{H}/roll-call", params={"day": str(today)}))
    assert {x["student_id"] for x in rc["residents"]} == {a, b} and not rc["taken"]
    out = await _ok(await app_client.post(f"{H}/roll-call", json={"day": str(today), "entries": [{"student_id": a, "status": "ABSENT"}, {"student_id": b, "status": "LEAVE"}]}))
    assert out["absent_reported"] == 1 and out["notified"] == 2
    again = await _ok(await app_client.post(f"{H}/roll-call", json={"day": str(today), "entries": [{"student_id": a, "status": "ABSENT"}]}))
    assert again["absent_reported"] == 0
    assert (await app_client.post(f"{H}/roll-call", json={"day": str(today.replace(year=today.year + 1)), "entries": []})).status_code == 422
    db_session.expire_all()
    parent_user = (await db_session.execute(select(Notification).where(Notification.title.like("%was not in the hostel at roll call")))).scalars().all()
    assert len(parent_user) == 1

    # the accounts office approves: each line becomes a charge in the student's dues
    officer = await _create_accounts_officer(app_client, db_session, code="ACCH")
    assert officer
    waiting = await _ok(await app_client.get(M, params={"status": "DRAFT"}))
    assert [x["id"] for x in waiting] == [d2["id"]]
    assert (await app_client.post(f"{M}/{d2['id']}/discard", json={"note": "x"})).status_code == 422
    ok = await _ok(await app_client.post(f"{M}/{d2['id']}/approve"))
    assert ok["charged"] == 3 and ok["total"] == 25000.0 and ok["skipped"] == 0
    assert (await app_client.post(f"{M}/{d2['id']}/approve")).status_code == 422          # decided once
    fa = await _fines(db_session, a, FineSource.HOSTEL)
    assert sorted(float(f.amount) for f in fa) == [5000.0, 10000.0]

    # prepared again for the same month: the charged stays are left out; the administrator's direct charge adds nothing
    await _login_admin(app_client)
    d3 = await _ok(await app_client.post(f"{M}/prepare", json={"kind": "HOSTEL", "month": str(month)}))
    assert d3["lines"] == [] and d3["total"] == 0.0
    assert (await _ok(await app_client.post(f"{H}/charges", json={"month": str(month)})))["students"] == 0
    assert len(await _fines(db_session, a, FineSource.HOSTEL)) == 2

    # the parent sees the notice
    await _parent_login(app_client, made["username"], made["temporary_password"])
    items = await _ok(await app_client.get("/api/v1/notifications/me"))
    assert any("roll call" in n["title"] for n in items)


async def test_transport_routes_riders_and_monthly_draft(app_client, admin_user, db_session):
    a, _ua = await _create_and_activate_student(app_client, cnic="76202-0000001-1")
    b, _ub = await _create_and_activate_student(app_client, cnic="76202-0000002-2")
    await _login_admin(app_client)
    roll_b = (await db_session.get(Student, uuid.UUID(b))).roll_number
    route = await _ok(await app_client.post(f"{T}/routes", json={"name": "Route 1", "seats": 1, "monthly_fee": 3000, "driver_name": "Aslam", "driver_phone": "0300"}), 201)
    assert (await app_client.post(f"{T}/routes", json={"name": "route 1", "monthly_fee": 1})).status_code == 409
    stop = await _ok(await app_client.post(f"{T}/routes/{route['id']}/stops", json={"name": "Model Town", "pickup_time": "07:10"}), 201)
    month = institute_today().replace(day=1)
    days = calendar.monthrange(month.year, month.month)[1]
    ra = await _ok(await app_client.post(f"{T}/riders", json={"route_id": route["id"], "student_id": a, "stop_id": stop["id"], "start_date": str(month)}), 201)
    full = await app_client.post(f"{T}/riders", json={"route_id": route["id"], "roll_number": roll_b})
    assert full.status_code == 422 and "full" in full.text
    r2 = await _ok(await app_client.post(f"{T}/routes", json={"name": "Route 2", "monthly_fee": 2000}), 201)
    late = month.replace(day=min(16, days))
    await _ok(await app_client.post(f"{T}/riders", json={"route_id": r2["id"], "roll_number": roll_b, "start_date": str(late)}), 201)
    assert (await app_client.post(f"{T}/riders", json={"route_id": r2["id"], "student_id": a})).status_code == 422    # already rides
    listed = await _ok(await app_client.get(f"{T}/routes"))
    assert [len(x["riders"]) for x in listed] == [1, 1]

    d = await _ok(await app_client.post(f"{M}/prepare", json={"kind": "TRANSPORT", "month": str(month)}))
    amounts = {ln["student_id"]: ln["amount"] for ln in d["lines"]}
    covered = days - late.day + 1
    assert amounts[a] == 3000.0
    assert amounts[b] == (round(2000 * covered / days) if covered < days else 2000.0)
    ok = await _ok(await app_client.post(f"{M}/{d['id']}/approve"))            # the administrator holds the approval right too
    assert ok["charged"] == 2
    fa = await _fines(db_session, uuid.UUID(a), FineSource.TRANSPORT)
    assert [float(f.amount) for f in fa] == [3000.0]
    again = await _ok(await app_client.post(f"{M}/prepare", json={"kind": "TRANSPORT", "month": str(month)}))
    assert again["lines"] == []

    # the student's home card shows the route, stop and pick-up time
    await _ok(await app_client.post(f"{T}/riders/{ra['id']}/end", json={}))
    assert (await app_client.post(f"{T}/riders/{ra['id']}/end", json={})).status_code == 422
    seat = await _ok(await app_client.post(f"{T}/riders", json={"route_id": route["id"], "student_id": a, "stop_id": stop["id"]}), 201)
    assert seat["id"]
    await _login(app_client, _ua, "StudentPass123!")
    card = (await _ok(await app_client.get("/api/v1/family/cards")))[0]
    assert card["transport"]["route"] == "Route 1" and card["transport"]["pickup_time"] == "07:10"


async def test_pages_render(app_client, admin_user):
    await _login_admin(app_client)
    for path in ("/dashboard/admin/transport", "/dashboard/admin/monthly-charges", "/dashboard/accounts/monthly-charges", "/dashboard/admin/hostel"):
        assert (await app_client.get(path)).status_code == 200, path
