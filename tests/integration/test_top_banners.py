"""Top bars: the administration shows a notice with a button to chosen people; they see it, press it or close it."""
from __future__ import annotations

import pytest

from app.banners.service import clean_url
from app.core.config import settings
from app.core.exceptions import ValidationAppError
from tests.integration.test_fees import _login_admin

pytestmark = pytest.mark.asyncio
B = "/api/v1/banners"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


def test_only_safe_links():
    assert clean_url("https://forms.gle/abc") == "https://forms.gle/abc"
    assert clean_url("skillsfuture.example.org/register?c=AI") == "https://skillsfuture.example.org/register?c=AI"
    assert clean_url("/dashboard/student/fees") == "/dashboard/student/fees"
    for bad in ("javascript:alert(1)", "data:text/html,hi", "//evil.example.com", "https://", "vbscript:x"):
        with pytest.raises(ValidationAppError):
            clean_url(bad)


async def test_a_bar_reaches_only_its_audience_and_records_responses(app_client, admin_user, db_session):
    from tests.integration.test_fees import _create_and_activate_student
    from tests.integration.test_purchasing import _as, _team

    team = await _team(app_client, db_session)
    sid, student_username = await _create_and_activate_student(app_client, cnic="78001-0000001-1")
    await _login_admin(app_client)
    body = {"message": "Free Google AI course: register by 30 Nov", "button_label": "Register", "button_url": "https://grow.google/ai",
            "tone": "green", "audience": {"roles": ["STUDENT"]}}
    made = await app_client.post(B, json=body)
    assert made.status_code == 201, made.text
    bar = made.json()
    assert bar["status"] == "SHOWING" and bar["reach"] == 1 and bar["audience_label"] == "All students"
    assert (await app_client.post(B, json={**body, "button_url": "javascript:alert(1)"})).status_code == 422
    assert (await app_client.post(B, json={**body, "audience": {}})).status_code == 422
    staff_bar = (await app_client.post(B, json={"message": "Staff meeting at 2 pm", "audience": {"roles": ["TEACHER"]}, "dismissible": False})).json()

    # the student sees the student bar only, presses it and closes it
    from tests.integration.test_fees import _login

    await _login(app_client, student_username, "StudentPass123!")
    mine = (await app_client.get(f"{B}/mine")).json()
    assert [m["id"] for m in mine] == [bar["id"]] and mine[0]["button_url"] == "https://grow.google/ai"
    assert mine[0]["dismissible"] is False                                   # students can never close a bar
    assert (await app_client.post(f"{B}/{bar['id']}/click")).status_code == 204
    assert (await app_client.post(f"{B}/{bar['id']}/dismiss")).status_code == 422
    assert len((await app_client.get(f"{B}/mine")).json()) == 1
    assert (await app_client.get(B)).status_code == 403

    # a teacher sees the staff bar, which cannot be closed
    await _as(app_client, team, "t1")
    mine = (await app_client.get(f"{B}/mine")).json()
    assert [m["id"] for m in mine] == [staff_bar["id"]] and mine[0]["dismissible"] is False
    await app_client.post(f"{B}/{staff_bar['id']}/dismiss")
    assert len((await app_client.get(f"{B}/mine")).json()) == 1

    # the administration sees who responded, hides the bar, and it disappears
    await _login_admin(app_client)
    listed = {b["id"]: b for b in (await app_client.get(B)).json()["bars"]}
    assert (listed[bar["id"]]["clicked"], listed[bar["id"]]["closed"]) == (1, 0)
    resp = (await app_client.get(f"{B}/{bar['id']}/responses")).json()
    assert resp[0]["clicks"] == 1 and resp[0]["dismissed_at"] is None
    csv = await app_client.get(f"{B}/{bar['id']}/responses.csv")
    assert csv.status_code == 200 and "Yes" in csv.text
    hidden = (await app_client.patch(f"{B}/{staff_bar['id']}", json={"is_active": False})).json()
    assert hidden["status"] == "HIDDEN"
    await _as(app_client, team, "t1")
    assert (await app_client.get(f"{B}/mine")).json() == []
    await _login_admin(app_client)
    assert (await app_client.delete(f"{B}/{bar['id']}")).status_code == 204


async def test_named_people_and_schedule(app_client, admin_user, db_session):
    from datetime import datetime, timedelta, timezone

    from tests.integration.test_purchasing import _as, _team

    team = await _team(app_client, db_session)
    await _login_admin(app_client)
    t2 = next(p for p in (await app_client.get(f"{B}/people", params={"q": "T02"})).json())
    later = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    future = (await app_client.post(B, json={"message": "Coming tomorrow", "audience": {"user_ids": [t2["user_id"]]}, "starts_at": later})).json()
    assert future["status"] == "SCHEDULED" and future["reach"] == 1
    now_bar = (await app_client.post(B, json={"message": "Only for you", "audience": {"user_ids": [t2["user_id"]]}})).json()
    await _as(app_client, team, "t2")
    assert [m["id"] for m in (await app_client.get(f"{B}/mine")).json()] == [now_bar["id"]]
    await _as(app_client, team, "t1")
    assert (await app_client.get(f"{B}/mine")).json() == []


def test_parents_of_chosen_students_see_the_bar():
    import uuid as _uuid

    from app.banners.models import TopBanner
    from app.banners.service import BannerService

    prog = str(_uuid.uuid4())
    bar = TopBanner(message="Fee reminder", audience={"program_ids": [prog], "semesters": [3], "include_parents": True}, is_active=True)
    svc = BannerService(None)
    parent = _uuid.uuid4()
    child_in = (_uuid.uuid4(), _uuid.UUID(prog), 3)
    child_out = (_uuid.uuid4(), _uuid.UUID(prog), 1)
    assert svc._matches(bar, parent, ["PARENT"], None, [child_out, child_in]) is True
    assert svc._matches(bar, parent, ["PARENT"], None, [child_out]) is False
    bar.audience = {**bar.audience, "include_parents": False}
    assert svc._matches(bar, parent, ["PARENT"], None, [child_in]) is False


def test_only_parents_may_close_a_bar():
    from app.banners.service import may_close

    assert may_close(["PARENT"]) is True
    for roles in (["STUDENT"], ["TEACHER"], ["ACCOUNTS_OFFICER"], ["ADMIN"], ["PARENT", "TEACHER"], []):
        assert may_close(roles) is False, roles