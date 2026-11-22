"""Morning summary (app/reports/morning.py): what it covers, when it is sent (8 AM, Monday to Saturday, once a day),
to whom (administrators), and the page."""
from __future__ import annotations

from datetime import date, datetime, time

import pytest
from sqlalchemy import delete, select

from app.approvals.models import ApprovalRequest
from app.core.config import settings
from app.notifications.email_models import ServiceHeartbeat
from app.notifications.models import Notification
from app.reports import morning
from tests.integration.test_attendance_and_grading import _login_admin

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


def test_days_covered():
    assert morning.covered(date(2026, 11, 24)) == (date(2026, 11, 23), date(2026, 11, 23))        # Tuesday: Monday
    assert morning.covered(date(2026, 11, 23)) == (date(2026, 11, 21), date(2026, 11, 22))        # Monday: Saturday and Sunday


def test_text():
    r = {"label": "Sat 21 Nov", "money": {"received": 45000, "payments": 3, "overdue": 120000, "defaulters": 4, "promises_today": 1},
         "attendance": {"classes": 10, "average": 86.5, "not_marked": 2, "staff": {"working": 9, "absent": 1, "late": 2, "leave": 0}},
         "waiting": {"approvals": 1, "applications": 3}, "problems": ["Disk 88% full."]}
    t = morning.render(r)
    assert "Rs. 45,000 received Sat 21 Nov (3 payment(s))" in t and "Overdue now Rs. 120,000 from 4 student(s). 1 promise(s)" in t
    assert "10 class(es), average 86.5%. 2 class(es) not marked. Staff: 1 absent, 2 late, 0 on leave." in t
    assert "Waiting: 1 approval, 3 applications." in t and "Problems: Disk 88% full." in t
    assert "Problems: none." in morning.render({**r, "problems": []})


async def test_sent_once_a_day_from_8_to_administrators_not_on_sunday(app_client, admin_user, db_session, monkeypatch):
    await db_session.execute(delete(ServiceHeartbeat).where(ServiceHeartbeat.name == morning.MARK))
    db_session.add(ApprovalRequest(kind="FINE_WAIVE", summary="Waive", payload={}, status="REQUESTED"))
    await db_session.flush()
    tuesday = date(2026, 11, 24)
    assert await morning.morning_round(db_session, now=datetime.combine(tuesday, time(7, 59))) == 0
    assert await morning.morning_round(db_session, now=datetime.combine(date(2026, 11, 22), time(9, 0))) == 0      # Sunday
    monkeypatch.setattr(settings, "morning_summary", 0)
    assert await morning.morning_round(db_session, now=datetime.combine(tuesday, time(8, 5))) == 0                # switched off
    monkeypatch.setattr(settings, "morning_summary", 1)
    assert await morning.morning_round(db_session, now=datetime.combine(tuesday, time(8, 5))) >= 1
    n = (await db_session.execute(select(Notification).where(Notification.user_id == admin_user.id, Notification.title == "Morning summary — Tue 24 Nov"))).scalars().all()
    assert len(n) == 1 and "Waiting:" in n[0].message and "approval" in n[0].message and n[0].link == morning.PAGE
    assert all(line.split(":")[0] in {"Money", "Attendance", "Waiting", "Problems"} for line in n[0].message.splitlines())
    assert await morning.morning_round(db_session, now=datetime.combine(tuesday, time(15, 0))) == 0               # once a day

    await _login_admin(app_client)
    page = await app_client.get("/api/v1/morning-summary")
    assert page.status_code == 200, page.text
    d = page.json()
    assert set(d) >= {"money", "attendance", "waiting", "problems", "text", "label", "last_sent"} and d["waiting"]["approvals"] >= 1
    html = (await app_client.get("/dashboard/admin/morning-summary")).text
    assert "morning-summary.js" in html and 'data-nav="morning-summary"' in html


async def test_only_administrators_see_the_page(app_client, admin_user, db_session):
    from tests.integration.test_fees import _create_accounts_officer

    await _login_admin(app_client)
    await _create_accounts_officer(app_client, db_session, code="MSACC")
    assert (await app_client.get("/api/v1/morning-summary")).status_code == 403
