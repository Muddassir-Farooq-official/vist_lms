"""Anonymous complaints (name hidden except to the filer; admin can reveal, and it is logged) + the charts' data."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.audit.models import AuditLog

pytestmark = pytest.mark.asyncio
ADMIN = {"username": "admin_test", "password": "AdminPass123!"}


@pytest.fixture(autouse=True)
def _no_login_throttle(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _login(client, username, password):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text


async def _student(client, n):
    r = await client.post("/api/v1/students", json={
        "first_name": f"Person{n}", "last_name": "Test", "date_of_birth": "2005-03-03", "gender": "Female", "cnic": f"35202-55000{n:02d}-1",
        "contact_no": f"0300555{n:04d}", "marital_status": "Single", "religion": "Islam", "nationality": "Pakistani", "guardian_name": "G",
        "guardian_cnic": f"35202-56000{n:02d}-2", "relation_with_guardian": "Father", "emergency_contact_no": "03001110001",
        "present_address": "x", "province": "Punjab", "district": "Lahore", "city": "Lahore"})
    assert r.status_code == 201, r.text
    return r.json()


async def _ready_student(client, n):
    """Create a student and make their account usable (the default password must be changed on first login)."""
    st = await _student(client, n)
    st["password"] = f"Chosen#Pass{n:02d}xx"
    return st


async def _become(client, st):
    await _login(client, st["username"], "vertex123")
    r = await client.post("/api/v1/auth/change-password", json={"current_password": "vertex123", "new_password": st["password"]})
    assert r.status_code == 204, r.text


async def _file(client, target_id, *, anonymous=False, reason="NEGLIGENCE", text="The lab was left unlocked repeatedly."):
    r = await client.post("/api/v1/complaints", json={"target_type": "STUDENT", "target_id": target_id, "reason": reason,
                                                      "description": text, "is_anonymous": anonymous})
    assert r.status_code == 201, r.text
    return r.json()


async def test_anonymous_complaint_hides_the_name_from_everyone_but_the_filer(app_client, admin_user, db_session):
    await _login(app_client, **ADMIN)
    filer, target = await _ready_student(app_client, 1), await _ready_student(app_client, 2)
    await _become(app_client, filer)
    anon = await _file(app_client, target["id"], anonymous=True)
    named = await _file(app_client, target["id"], anonymous=False, reason="MISBEHAVIOR")
    # the person who filed always sees themselves
    assert anon["is_anonymous"] is True and anon["complainant_name"].startswith("Person1")
    mine = {c["id"]: c for c in (await app_client.get("/api/v1/complaints/me")).json()}
    assert mine[anon["id"]]["complainant_name"].startswith("Person1") and mine[anon["id"]]["is_anonymous"] is True

    # the administrator's screens say "Anonymous" -- no name, no id, no student/staff hint -- until they reveal it
    await _login(app_client, **ADMIN)
    seen = {c["id"]: c for c in (await app_client.get("/api/v1/complaints")).json()}
    a = seen[anon["id"]]
    assert (a["complainant_name"], a["complainant_identifier"], a["complainant_id"], a["complainant_type"], a["is_anonymous"]) == ("Anonymous", None, None, None, True)
    assert seen[named["id"]]["complainant_name"].startswith("Person1") and seen[named["id"]]["complainant_id"] == filer["id"]
    one = (await app_client.get(f"/api/v1/complaints/{anon['id']}")).json()
    assert one["complainant_name"] == "Anonymous" and one["complainant_id"] is None
    updated = await app_client.post(f"/api/v1/complaints/{anon['id']}/status", json={"status": "UNDER_REVIEW", "note": "Looking into it"})
    assert updated.status_code == 200 and updated.json()["complainant_name"] == "Anonymous"      # even the reply to an admin action stays masked

    # the person complained about sees nothing at all
    await _become(app_client, target)
    assert (await app_client.get("/api/v1/complaints")).status_code == 403
    assert (await app_client.get(f"/api/v1/complaints/{anon['id']}")).status_code == 403
    assert (await app_client.get("/api/v1/complaints/me")).json() == []
    assert (await app_client.post(f"/api/v1/complaints/{anon['id']}/reveal-identity")).status_code == 403


async def test_an_admin_can_reveal_the_name_and_it_is_recorded(app_client, admin_user, db_session):
    await _login(app_client, **ADMIN)
    filer, target = await _ready_student(app_client, 3), await _ready_student(app_client, 4)
    await _become(app_client, filer)
    anon, named = await _file(app_client, target["id"], anonymous=True), await _file(app_client, target["id"])
    await _login(app_client, **ADMIN)
    r = await app_client.post(f"/api/v1/complaints/{anon['id']}/reveal-identity")
    assert r.status_code == 200
    body = r.json()
    assert body["complainant_name"].startswith("Person3") and body["complainant_id"] == filer["id"] and body["complainant_type"] == "STUDENT"
    logged = (await db_session.execute(select(AuditLog).where(AuditLog.action == "complaint.identity_revealed"))).scalars().all()
    assert len(logged) == 1 and str(logged[0].entity_id) == anon["id"]
    # looking up a complaint that was NOT anonymous needs no log entry
    assert (await app_client.post(f"/api/v1/complaints/{named['id']}/reveal-identity")).status_code == 200
    again = (await db_session.execute(select(AuditLog).where(AuditLog.action == "complaint.identity_revealed"))).scalars().all()
    assert len(again) == 1


async def test_filing_defaults_to_named_and_the_filer_can_still_withdraw_an_anonymous_one(app_client, admin_user, db_session):
    await _login(app_client, **ADMIN)
    filer, target = await _ready_student(app_client, 5), await _ready_student(app_client, 6)
    await _become(app_client, filer)
    plain = (await app_client.post("/api/v1/complaints", json={"target_type": "STUDENT", "target_id": target["id"], "reason": "NEGLIGENCE",
                                                                "description": "Nothing about anonymity here."})).json()
    assert plain["is_anonymous"] is False
    anon = await _file(app_client, target["id"], anonymous=True)
    w = await app_client.post(f"/api/v1/complaints/{anon['id']}/withdraw", json={"note": "sorted out"})
    assert w.status_code == 200 and w.json()["status"] == "WITHDRAWN" and w.json()["complainant_name"].startswith("Person5")


async def test_the_charts_have_correct_numbers(app_client, admin_user, db_session):
    await _login(app_client, **ADMIN)
    filer, other, target = await _ready_student(app_client, 7), await _ready_student(app_client, 8), await _ready_student(app_client, 9)
    await _become(app_client, filer)
    c1 = await _file(app_client, target["id"], anonymous=True, reason="HARASSMENT")
    c2 = await _file(app_client, target["id"], reason="HARASSMENT")
    c3 = await _file(app_client, target["id"], reason="FACILITY_ISSUE")
    await _become(app_client, other)
    c4 = await _file(app_client, target["id"], anonymous=True, reason="NEGLIGENCE")
    assert (await app_client.get("/api/v1/complaints/analytics/overview")).status_code == 403          # students cannot see the charts
    await _login(app_client, **ADMIN)
    assert (await app_client.post(f"/api/v1/complaints/{c1['id']}/status", json={"status": "RESOLVED", "note": "done"})).status_code == 200
    assert (await app_client.post(f"/api/v1/complaints/{c2['id']}/status", json={"status": "UNDER_REVIEW"})).status_code == 200
    assert (await app_client.post(f"/api/v1/complaints/{c3['id']}/status", json={"status": "DISMISSED", "note": "no case"})).status_code == 200

    data = (await app_client.get("/api/v1/complaints/analytics/overview")).json()
    k = data["kpis"]
    assert (k["total"], k["open"], k["under_review"], k["resolved"], k["dismissed"], k["withdrawn"]) == (4, 2, 1, 1, 1, 0)
    assert (k["anonymous"], k["anonymous_pct"]) == (2, 50.0)
    assert k["filed_last_30_days"] == 4 and k["filed_previous_30_days"] == 0 and k["oldest_open_days"] == 0
    assert k["avg_resolution_hours"] is not None and k["avg_resolution_hours"] >= 0
    assert {s["key"]: s["count"] for s in data["by_status"]} == {"SUBMITTED": 1, "UNDER_REVIEW": 1, "RESOLVED": 1, "DISMISSED": 1, "WITHDRAWN": 0}
    assert [(r["key"], r["count"]) for r in data["by_reason"]] == [("HARASSMENT", 2), ("FACILITY_ISSUE", 1), ("NEGLIGENCE", 1)] or \
        data["by_reason"][0] == {"key": "HARASSMENT", "label": data["by_reason"][0]["label"], "count": 2}
    assert len(data["by_week"]) == 12 and data["by_week"][-1]["filed"] == 4 and data["by_week"][-1]["closed"] == 2
    assert sum(w["filed"] for w in data["by_week"]) == 4
    assert {x["key"]: x["count"] for x in data["filed_by"]} == {"STUDENT": 4} and {x["key"]: x["count"] for x in data["filed_against"]} == {"STUDENT": 4}


async def test_the_screens_ship_the_anonymous_option_and_the_charts(app_client):
    for path in ("/dashboard/student/complaints", "/dashboard/faculty/complaints"):
        html = (await app_client.get(path)).text
        assert 'id="complaint-anonymous"' in html and "anonymous" in html.lower(), path
    admin = (await app_client.get("/dashboard/admin/complaints")).text
    for needle in ('data-tab="overview"', 'data-tab="all"', 'data-tab="people"', 'id="chart-status"', 'id="chart-reason"', 'id="chart-weekly"',
                   'id="kpi-open"', "charts.js"):
        assert needle in admin, needle
    charts = (await app_client.get("/static/js/charts.js")).text
    for needle in ("donut", "hbars", "lineChart"):
        assert needle in charts, needle
    js = (await app_client.get("/static/js/complaints-admin.js")).text
    assert "reveal-identity" in js and "analytics/overview" in js
    assert "complaint-anonymous" in (await app_client.get("/static/js/complaints-portal.js")).text