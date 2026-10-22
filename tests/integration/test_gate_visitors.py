"""The gate is a visitor log book: in, out, returning visitors by CNIC, search, corrections and export."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.gate.models import VisitorEntry
from tests.integration.test_announcements import _first_login, _staff
from tests.integration.test_fees import _login_admin

pytestmark = pytest.mark.asyncio
G = "/api/v1/gate"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)


async def _guard(client, db):
    await _login_admin(client)
    user, first = await _staff(client, db, "GRD", "SECURITY_GUARD", "35202-7800001-1")
    await _first_login(client, user, first)


async def test_visitor_in_and_out_and_the_log_book(app_client, admin_user, db_session):
    await _guard(app_client, db_session)
    v = await app_client.post(f"{G}/visitors", json={"visitor_name": "Imran Ali", "cnic": "35202-1234567-1", "phone": "0300-1234567", "purpose": "Admission inquiry",
                                                      "person_to_meet": "Admissions office", "vehicle_no": "LEA-1234", "people_count": 2})
    assert v.status_code == 201, v.text
    e = v.json()
    assert e["reference"].startswith("VIS-") and e["cnic"] == "35202-1234567-1" and e["phone"] == "03001234567" and e["inside"]
    again = await app_client.post(f"{G}/visitors", json={"visitor_name": "Imran Ali", "cnic": "3520212345671", "purpose": "Meeting"})
    assert again.status_code == 422 and "already inside" in again.json()["error"]["message"]
    assert (await app_client.post(f"{G}/visitors", json={"visitor_name": "X Y", "cnic": "12345", "purpose": "Meeting"})).status_code == 422
    s = (await app_client.get(f"{G}/summary")).json()
    assert (s["inside_now"], s["entries_today"], s["people_today"]) == (1, 1, 2)
    assert [x["id"] for x in (await app_client.get(f"{G}/inside")).json()] == [e["id"]]

    out = await app_client.post(f"{G}/visitors/{e['id']}/out")
    assert out.status_code == 200 and not out.json()["inside"] and out.json()["exit_time"]
    assert (await app_client.post(f"{G}/visitors/{e['id']}/out")).status_code == 422
    back = (await app_client.get(f"{G}/visitors/by-cnic/35202-1234567-1")).json()
    assert back["visitor_name"] == "Imran Ali" and back["visits"] == 1
    assert [x["id"] for x in (await app_client.get(f"{G}/visitors", params={"q": "1234567"})).json()] == [e["id"]]
    assert [x["id"] for x in (await app_client.get(f"{G}/visitors", params={"q": "lea-12"})).json()] == [e["id"]]

    # a guard sees the last 7 days only; corrections and export are for the administration
    await db_session.execute(update(VisitorEntry).values(entry_at=datetime.now(timezone.utc) - timedelta(days=20), exit_at=None))
    await db_session.commit()
    assert (await app_client.get(f"{G}/visitors")).json() == []
    assert (await app_client.patch(f"{G}/visitors/{e['id']}", json={"purpose": "Fee deposit"})).status_code == 403
    assert (await app_client.get(f"{G}/visitors.csv")).status_code == 403

    await _login_admin(app_client)
    old = (await app_client.get(f"{G}/visitors", params={"from_date": (datetime.now() - timedelta(days=30)).date().isoformat()})).json()
    assert [x["id"] for x in old] == [e["id"]]
    fixed = await app_client.patch(f"{G}/visitors/{e['id']}", json={"purpose": "Fee deposit", "exit_time": "23:59"})
    assert fixed.status_code == 200 and fixed.json()["purpose"] == "Fee deposit" and not fixed.json()["inside"]
    csv = await app_client.get(f"{G}/visitors.csv", params={"from_date": (datetime.now() - timedelta(days=30)).date().isoformat()})
    assert csv.status_code == 200 and "Imran Ali" in csv.text and "35202-1234567-1" in csv.text


async def test_old_student_and_staff_gate_check_in_is_gone(app_client, admin_user, db_session):
    await _login_admin(app_client)
    for path in ("/api/v1/gate/people", "/api/v1/gate/settings", "/api/v1/gate/schedules", "/api/v1/gate/report"):
        assert (await app_client.get(path)).status_code == 404, path
    assert (await app_client.post("/api/v1/gate/check-in", json={})).status_code in (404, 405)
    page = (await app_client.get("/dashboard/admin/gate")).text
    assert "gate-visitors.js" in page and "Staff timings" not in page