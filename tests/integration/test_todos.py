"""Daily to-do lists: staff write and update their own; older days are locked; the administration reads a monthly report and CSV."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.clock import institute_today
from app.core.config import settings
from tests.integration.test_fees import _login_admin

pytestmark = pytest.mark.asyncio
T = "/api/v1/todos"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def test_my_daily_list_carry_over_and_locked_days(app_client, admin_user, db_session):
    from tests.integration.test_purchasing import _as, _team

    team = await _team(app_client, db_session)
    await _as(app_client, team, "t1")
    today = institute_today()
    a = await app_client.post(T, json={"title": "Check Fall attendance sheets", "note": "Rooms 1-4"})
    assert a.status_code == 201, a.text
    b = (await app_client.post(T, json={"title": "Prepare quiz 3"})).json()
    y = (await app_client.post(T, json={"title": "Call the lab vendor", "work_date": str(today - timedelta(days=1))})).json()
    assert (await app_client.post(T, json={"title": "Too old", "work_date": str(today - timedelta(days=2))})).status_code == 422
    assert (await app_client.patch(f"{T}/{a.json()['id']}", json={"status": "DONE"})).json()["done_at"]
    assert (await app_client.patch(f"{T}/{b['id']}", json={"status": "FINISHED"})).status_code == 422
    day = (await app_client.get(f"{T}/me")).json()
    assert (day["done"], day["total"]) == (1, 2) and day["unfinished_before"]["count"] == 1
    moved = (await app_client.post(f"{T}/carry-over", json={})).json()
    assert moved["moved"] == 1
    day = (await app_client.get(f"{T}/me")).json()
    assert day["total"] == 3 and any(i["carried"] and i["title"] == "Call the lab vendor" for i in day["items"])
    old = (await app_client.get(f"{T}/me", params={"day": str(today - timedelta(days=1))})).json()
    assert old["items"][0]["status"] == "CARRIED_OVER" and old["items"][0]["editable"] is False
    assert (await app_client.patch(f"{T}/{y['id']}", json={"status": "DONE"})).status_code == 422       # carried items stay as they were
    assert (await app_client.delete(f"{T}/{a.json()['id']}")).status_code == 422                         # done items are not removed
    assert (await app_client.delete(f"{T}/{b['id']}")).status_code == 204
    # someone else's item is invisible
    await _as(app_client, team, "t2")
    assert (await app_client.patch(f"{T}/{a.json()['id']}", json={"status": "NOT_DONE"})).status_code == 404
    assert (await app_client.get(f"{T}/report")).status_code == 403

    await _login_admin(app_client)
    rep = (await app_client.get(f"{T}/report", params={"month": f"{today:%Y-%m}"})).json()
    t1 = next(p for p in rep["people"] if p["items"])
    assert t1["done"] == 1 and t1["items"] == 2 and t1["carried"] == (1 if (today - timedelta(days=1)).month == today.month else 0)
    assert any(p["items"] == 0 for p in rep["people"])                     # staff without a list are shown too
    person = (await app_client.get(f"{T}/report/person/{t1['user_id']}", params={"month": f"{today:%Y-%m}"})).json()
    assert person["days"][0]["date"] == str(today)
    csv = await app_client.get(f"{T}/report.csv", params={"month": f"{today:%Y-%m}"})
    assert csv.status_code == 200 and "attachment" in csv.headers["content-disposition"]
    assert "Check Fall attendance sheets" in csv.text and "Done" in csv.text
    assert (await app_client.get(f"{T}/report", params={"month": "2026-13"})).status_code == 422


async def test_csv_neutralises_formulas(app_client, admin_user, db_session):
    from tests.integration.test_purchasing import _as, _team

    team = await _team(app_client, db_session)
    await _as(app_client, team, "t1")
    assert (await app_client.post(T, json={"title": "=HYPERLINK(\"http://x\")"})).status_code == 201
    await _login_admin(app_client)
    csv = (await app_client.get(f"{T}/report.csv")).text
    assert "'=HYPERLINK" in csv


async def test_month_pdf_and_daily_reminder(app_client, admin_user, db_session, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from sqlalchemy import select

    import app.todos.service as todo_service
    from app.notifications.models import Notification
    from tests.integration.test_purchasing import _as, _team

    team = await _team(app_client, db_session)
    await _as(app_client, team, "t1")
    assert (await app_client.post(T, json={"title": "Mark the BSCS-3 quizzes", "note": "Section A"})).status_code == 201
    await _login_admin(app_client)
    pdf = await app_client.get(f"{T}/report.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF" and "attachment" in pdf.headers["content-disposition"]

    # 4 pm on a Wednesday: t2 (no list today) is reminded once; t1 (has a list) is not
    wednesday = datetime(2026, 11, 4, 16, 0, tzinfo=ZoneInfo(settings.institute_timezone))
    monkeypatch.setattr(todo_service, "institute_now", lambda: wednesday)
    monkeypatch.setattr(todo_service, "institute_today", lambda: wednesday.date())
    monkeypatch.setattr(settings, "todo_reminder_hour", 15)
    from app.todos.models import TodoItem

    for item in (await db_session.execute(select(TodoItem))).scalars():
        item.work_date = wednesday.date()                    # t1's list counts for that day
    await db_session.flush()
    first = await todo_service.remind_missing_lists(db_session)
    again = await todo_service.remind_missing_lists(db_session)
    assert first >= 1 and again == 0
    told = (await db_session.execute(select(Notification).where(Notification.title == todo_service.REMINDER_TITLE))).scalars().all()
    assert all(n.link.endswith("/tasks?day=2026-11-04#todo") for n in told)
    monkeypatch.setattr(settings, "todo_reminder_hour", 0)
    assert await todo_service.remind_missing_lists(db_session) == 0