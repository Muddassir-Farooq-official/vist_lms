"""Daily review of money changes (app/audit/money_review.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from tests.integration.test_scholarship_end_and_edit import SC, _with_student_card

pytestmark = pytest.mark.asyncio
MR = "/api/v1/audit/money-review"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    monkeypatch.setattr(settings, "rate_limit_login_per_username_per_hour", 1000)


async def test_a_discount_on_every_fee_shows_up_with_the_student_and_can_be_ticked(app_client, admin_user, db_session):
    from app.core.clock import institute_today

    sid, tuition, card, types = await _with_student_card(app_client, "75001-0000001-1")
    r = await app_client.post(SC, json={"student_id": sid, "name": "Need based", "kind": "FIXED", "value": 2000, "valid_from": "2026-01-01",
                                        "reason": "Given on every fee by mistake"})
    assert r.status_code == 201, r.text
    today = institute_today().isoformat()
    d = (await app_client.get(MR, params={"day": today})).json()
    (item,) = [i for i in d["items"] if i["kind"] == "scholarship.granted"]
    assert item["label"] == "Discount given" and item["by"] == "admin_test"
    assert "Rs. 2,000 off every fee" in item["detail"] and item["student"] and not item["checked"]
    assert d["by_group"]["Discounts"] >= 1

    assert (await app_client.post(f"{MR}/check", json={"ids": [item["id"]], "note": "Narrowed to tuition"})).json() == {"checked": 1}
    assert (await app_client.post(f"{MR}/check", json={"ids": [item["id"]]})).json() == {"checked": 0}       # once
    d = (await app_client.get(MR, params={"day": today})).json()
    (item,) = [i for i in d["items"] if i["kind"] == "scholarship.granted"]
    assert item["checked"] and item["checked_by"] == "admin_test" and item["check_note"] == "Narrowed to tuition"
    only_open = (await app_client.get(MR, params={"day": today, "unchecked_only": "true"})).json()
    assert all(i["id"] != item["id"] for i in only_open["items"])


async def test_only_back_dated_payments_are_listed(app_client, admin_user, db_session):
    from app.audit.models import AuditLog
    from app.core.clock import institute_today
    from tests.integration.test_fees import _login_admin

    await _login_admin(app_client)
    now = datetime.now(timezone.utc)
    today = institute_today()
    db_session.add_all([
        AuditLog(actor_user_id=admin_user.id, action="fee.payment_recorded", entity_type="fee_assignment", entity_id=None,
                 after={"amount": 5000, "paid_on": str(today), "receipt_number": "R-1"}, created_at=now, updated_at=now),
        AuditLog(actor_user_id=admin_user.id, action="fee.payment_recorded", entity_type="fee_assignment", entity_id=None,
                 after={"amount": 7000, "paid_on": str(today - timedelta(days=12)), "receipt_number": "R-2"}, created_at=now, updated_at=now),
    ])
    await db_session.commit()
    d = (await app_client.get(MR, params={"day": today.isoformat()})).json()
    paid = [i for i in d["items"] if i["kind"] == "fee.payment_recorded"]
    assert [i["detail"] for i in paid] == [f"Rs. 7,000 dated {today - timedelta(days=12)} (receipt R-2)"]
    assert paid[0]["label"] == "Back-dated payment"


async def test_only_administrators_see_it(app_client, admin_user, db_session):
    from tests.integration.test_fees import _create_accounts_officer

    await _create_accounts_officer(app_client, db_session)
    assert (await app_client.get(MR)).status_code == 403


async def test_the_morning_notice_goes_once_with_yesterdays_count(app_client, admin_user, db_session, monkeypatch):
    from app.audit import money_review as mr
    from app.audit.models import AuditLog
    from app.notifications.models import Notification

    yesterday_noon = datetime.now(timezone.utc) - timedelta(days=1)
    yesterday_noon = yesterday_noon.replace(hour=7, minute=0)                 # 12:00 in Pakistan
    db_session.add(AuditLog(actor_user_id=admin_user.id, action="fine.waived", entity_type="fine", entity_id=None, before={"status": "PENDING"},
                            after={"status": "WAIVED", "reason": "Hardship"}, created_at=yesterday_noon, updated_at=yesterday_noon))
    await db_session.commit()
    monkeypatch.setattr("app.core.clock.institute_now", lambda: datetime.now(timezone.utc).replace(tzinfo=None, hour=9) )
    assert await mr.morning_notice(db_session) == 1
    assert await mr.morning_notice(db_session) == 0                            # once a day
    await db_session.commit()
    (n,) = (await db_session.execute(select(Notification).where(Notification.user_id == admin_user.id,
                                                                Notification.title.like("%money change%")))).scalars().all()
    assert n.title.startswith("1 money change") and "fines waived" in n.message and n.link == "/dashboard/admin/activity#money"
