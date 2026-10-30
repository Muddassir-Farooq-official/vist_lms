"""Basic salary can be left empty at hiring and set or corrected later (up or down, from a date); payroll uses the right
amount. The same task can be assigned to several people at once."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.employees.models import JobRole
from tests.integration.test_file_download_authorization import _employee_payload, _login_admin
from tests.integration.test_staff_role import _create_staff

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def _new_employee(client, db, *, cnic, **over):
    job = JobRole(code=f"J{cnic[-3:]}", title="Clerk", default_role_code="STAFF")
    db.add(job)
    await db.commit()
    await _login_admin(client)
    payload = _employee_payload(job.id, cnic=cnic, hire_date="2026-08-01", **over)
    payload.pop("base_salary", None) if over.get("base_salary") is None else None
    r = await client.post("/api/v1/employees", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _history(client, eid):
    return (await client.get(f"/api/v1/employees/{eid}/salary-history")).json()


async def _paid(db, eid, month: date):
    from app.payroll.service import PayrollService

    return float((await PayrollService(db)._salaries([uuid.UUID(eid)], month)).get(uuid.UUID(eid), 0))


async def test_salary_can_be_left_empty_at_hiring_and_set_later(app_client, admin_user, db_session):
    eid = await _new_employee(app_client, db_session, cnic="83001-0000001-1", base_salary=None)
    assert [float(h["base_salary"]) for h in await _history(app_client, eid)] == [0.0]
    inc = await app_client.post(f"/api/v1/employees/{eid}/salary-increment", json={"change_type": "PERCENTAGE", "value": 10})
    assert inc.status_code == 422 and "Set basic salary" in inc.text                       # nothing to increase yet
    short = await app_client.post(f"/api/v1/employees/{eid}/salary", json={"amount": 60000, "reason": "x"})
    assert short.status_code == 422                                                        # the reason is required
    r = await app_client.post(f"/api/v1/employees/{eid}/salary", json={"amount": 60000, "effective_from": "2026-08-01", "reason": "Salary agreed at interview"})
    assert r.status_code == 200, r.text
    assert r.json()["change_type"] in ("SET", "CORRECTED") and r.json()["base_salary"] == 60000
    assert await _paid(db_session, eid, date(2026, 9, 1)) == 60000


async def test_a_wrong_salary_can_be_corrected_down_from_its_own_start_date(app_client, admin_user, db_session):
    eid = await _new_employee(app_client, db_session, cnic="83001-0000002-2", base_salary=85000)
    assert await _paid(db_session, eid, date(2026, 9, 1)) == 85000
    early = await app_client.post(f"/api/v1/employees/{eid}/salary", json={"amount": 58000, "effective_from": "2026-07-01", "reason": "Typo at hiring"})
    assert early.status_code == 422                                                        # cannot start before the salary on record
    same = await app_client.post(f"/api/v1/employees/{eid}/salary", json={"amount": 85000, "reason": "No change at all"})
    assert same.status_code == 422
    fix = await app_client.post(f"/api/v1/employees/{eid}/salary", json={"amount": 58000, "effective_from": "2026-08-01", "reason": "Typo at hiring: 58,000 not 85,000"})
    assert fix.status_code == 200, fix.text
    assert fix.json()["change_type"] == "CORRECTED"
    assert await _paid(db_session, eid, date(2026, 8, 1)) == 58000                          # the corrected amount, from the start
    hist = await _history(app_client, eid)
    assert sorted(float(h["base_salary"]) for h in hist) == [58000.0, 85000.0]              # the wrong amount stays on record
    later = await app_client.post(f"/api/v1/employees/{eid}/salary", json={"amount": 55000, "effective_from": "2026-10-01", "reason": "Fewer hours from October"})
    assert later.status_code == 200 and later.json()["change_type"] == "SET"
    assert await _paid(db_session, eid, date(2026, 9, 1)) == 58000 and await _paid(db_session, eid, date(2026, 11, 1)) == 55000


async def test_the_same_task_goes_to_several_people_each_with_their_own_copy(app_client, admin_user, db_session):
    from sqlalchemy import select

    from app.auth.models import User
    from app.notifications.models import Notification

    a_id, a_user = await _create_staff(app_client, db_session, cnic="83001-0000003-3")
    job = JobRole(code="STAFF_U", title="Office Assistant 2", default_role_code="STAFF")
    db_session.add(job)
    await db_session.commit()
    await _login_admin(app_client)
    b = await app_client.post("/api/v1/employees", json=_employee_payload(job.id, cnic="83001-0000004-4"))
    assert b.status_code == 201, b.text
    users = {u.username: u.id for u in (await db_session.execute(select(User))).scalars()}
    me = users["admin_test"]
    deadline = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    body = {"title": "Submit the stock count", "description": "Count your department's stock and upload the sheet.", "priority": "HIGH", "deadline": deadline}
    none = await app_client.post("/api/v1/tasks/bulk", json={**body, "assignee_user_ids": [str(me)]})
    assert none.status_code == 422                                                         # only yourself: nothing to create
    r = await app_client.post("/api/v1/tasks/bulk", json={**body, "assignee_user_ids": [str(users[a_user]), str(users[b.json()["username"]]), str(me)]})
    assert r.status_code == 201, r.text
    out = r.json()
    assert len(out["created"]) == 2 and len(out["skipped"]) == 1 and "that is you" in out["skipped"][0]
    sent = (await app_client.get("/api/v1/tasks/sent")).json()
    assert sum(1 for t in sent if t["title"] == "Submit the stock count") == 2
    told = (await db_session.execute(select(Notification.user_id).where(Notification.title.like("New task from%")))).scalars().all()
    assert {users[a_user], users[b.json()["username"]]} <= set(told)