"""SEC-03: evidence of failed sign-ins and wrong reset codes is kept even though the request itself fails."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditLog
from app.auth.models import LoginAttempt
from app.core.config import settings

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)


async def _count(engine, model, *where):
    async with AsyncSession(bind=engine) as s:                     # a fresh connection: sees only committed rows
        return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


async def test_failed_sign_in_is_kept_after_the_request_is_rolled_back(app_client, admin_user, db_session):
    engine = db_session.bind
    r = await app_client.post("/api/v1/auth/login", json={"username": "admin_test", "password": "wrong-password"}, headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 401
    await db_session.rollback()                                    # what get_db does after an error in production
    assert await _count(engine, LoginAttempt, LoginAttempt.username_attempted == "admin_test", LoginAttempt.success.is_(False)) == 1
    assert await _count(engine, AuditLog, AuditLog.action == "auth.login_failed") == 1
    r = await app_client.post("/api/v1/auth/login", json={"username": "nobody-here", "password": "x" * 12})
    assert r.status_code == 401
    await db_session.rollback()
    assert await _count(engine, LoginAttempt, LoginAttempt.username_attempted == "nobody-here") == 1


async def test_deactivated_account_sign_in_is_recorded(app_client, admin_user, db_session):
    from app.auth.models import User

    u = User(username="gone_user", password_hash=admin_user.password_hash, is_active=False, must_change_password=False)
    db_session.add(u)
    await db_session.commit()
    r = await app_client.post("/api/v1/auth/login", json={"username": "gone_user", "password": "AdminPass123!"})
    assert r.status_code == 401
    await db_session.rollback()
    assert await _count(db_session.bind, AuditLog, AuditLog.action == "auth.login_blocked_inactive") == 1


async def test_five_wrong_reset_codes_finish_the_code_even_though_each_request_fails(app_client, admin_user, db_session):
    import uuid
    from datetime import datetime, timedelta, timezone

    from app.auth.models import PasswordResetToken
    from app.auth.reset import _hash
    from app.core.redis import get_redis

    tid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    db_session.add(PasswordResetToken(id=tid, user_id=admin_user.id, token_hash=_hash(tid, "123456"), expires_at=now + timedelta(minutes=10), created_at=now))
    await db_session.commit()
    await get_redis().delete(f"pwreset:tries:{admin_user.id}")
    for _ in range(5):
        r = await app_client.post("/api/v1/auth/reset-password", json={"username": "admin_test", "code": "000000", "new_password": "BrandNewPass123!"})
        assert r.status_code == 422
        await db_session.rollback()
    async with AsyncSession(bind=db_session.bind) as s:
        assert (await s.get(PasswordResetToken, tid)).used_at is not None
    assert await _count(db_session.bind, AuditLog, AuditLog.action == "auth.password_reset_wrong_code") == 5
    ok = await app_client.post("/api/v1/auth/reset-password", json={"username": "admin_test", "code": "123456", "new_password": "BrandNewPass123!"})
    assert ok.status_code == 422                                       # the right code no longer works: it was finished


def test_migration_check_flags_destructive_migrations_but_not_permission_changes():
    import importlib.util
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("cmp", root / "scripts" / "check_migration_path.py")
    cmp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cmp)
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    assert cmp.destructive_lines(script.get_revision("e8c3b5a1f492").module)            # drops the old payroll tables
    assert cmp.destructive_lines(script.get_revision("a2e9c5f18b6d").module)            # wipes attendance
    assert cmp.destructive_lines(script.get_revision("f1e2d3c4b5a6").module) == []      # only removes two permission rows
    assert cmp.destructive_lines(script.get_revision("a7b8c9d0e1f2").module) == []


def test_secrets_check_passes_on_the_repository():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    r = subprocess.run([sys.executable, "scripts/check_no_secrets.py"], cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


async def test_removing_a_timetable_slot_keeps_past_unmarked_classes(app_client, admin_user):
    """ACAD-01: past classes with no attendance are gaps to fix, not rows to delete."""
    from datetime import timedelta

    from app.core.clock import institute_today
    from tests.integration.test_fees import _login_admin
    from tests.integration.test_timetable_conflicts import _DAY_NAMES, _get_or_create_period

    await _login_admin(app_client)
    start, end = institute_today() - timedelta(days=21), institute_today() + timedelta(days=21)
    course = (await app_client.post("/api/v1/academics/courses", json={"code": "GAP-C", "title": "Gap course", "credit_hours": 3})).json()
    off = (await app_client.post("/api/v1/academics/offerings", json={"course_id": course["id"], "term_label": "Fall 2026",
                                                                      "term_start_date": str(start), "term_end_date": str(end)})).json()
    period_id = await _get_or_create_period(app_client, "11:00:00", "12:00:00")
    slot = await app_client.post(f"/api/v1/academics/offerings/{off['id']}/timetable-slots",
                                 json={"day_of_week": _DAY_NAMES[(institute_today() - timedelta(days=1)).weekday()], "period_id": period_id, "room": "Room 90"})
    assert slot.status_code == 201, slot.text
    before = (await app_client.get(f"/api/v1/academics/offerings/{off['id']}/class-sessions")).json()
    past = [s for s in before if s["session_date"] < str(institute_today())]
    assert past
    assert (await app_client.delete(f"/api/v1/academics/timetable-slots/{slot.json()['slot']['id']}")).status_code == 200
    after = (await app_client.get(f"/api/v1/academics/offerings/{off['id']}/class-sessions")).json()
    assert {s["id"] for s in past} <= {s["id"] for s in after}                       # the past classes are still there to be marked
    assert all(s["session_date"] < str(institute_today()) for s in after)


async def test_one_time_password_is_kept_only_until_the_slip_is_printed(app_client, admin_user, db_session):
    """SEC-04."""
    from app.auth.models import User
    from tests.integration.test_fees import _login_admin
    from tests.integration.test_module1_students import _make_student

    await _login_admin(app_client)
    created = await _make_student(app_client, "35202-3999001-1")
    first = await app_client.get(f"/api/v1/students/{created['id']}/admission-slip")
    assert "Temporary password" in first.text
    second = await app_client.get(f"/api/v1/students/{created['id']}/admission-slip")
    assert "Already printed once" in second.text
    user = (await db_session.execute(select(User).where(User.username == created["username"]))).scalar_one()
    await db_session.refresh(user)
    assert user.temp_password is None and user.must_change_password
