"""Slow pages must be easy to diagnose: every response says how long it took; slow ones are logged with their query count."""
from __future__ import annotations

import pytest

import app.main as main_module
from app.core import migration_guard as guard
from app.core.config import settings
from tests.integration.test_module3_sections_allocation import _admin

pytestmark = pytest.mark.asyncio


class _Logger:
    def __init__(self):
        self.calls = []

    def warning(self, event, **kw):
        self.calls.append((event, kw))

    def __getattr__(self, name):          # info / error / debug: ignore
        return lambda *a, **k: None


async def test_every_response_reports_how_long_it_took(app_client):
    r = await app_client.get("/health/live")
    assert r.headers["x-response-time"].endswith("ms") and int(r.headers["x-response-time"][:-2]) >= 0


async def test_a_slow_request_is_logged_with_its_database_query_count(app_client, admin_user, db_session, monkeypatch):
    spy = _Logger()
    monkeypatch.setattr(main_module, "logger", spy)
    monkeypatch.setattr(settings, "slow_request_ms", 0)                 # treat every request as slow
    # the tests run on their own database connection; give it the same query counter the real app has
    from sqlalchemy import event

    import app.core.database as database

    test_engine = db_session.bind.sync_engine
    event.listen(test_engine, "before_cursor_execute", database._count_query)
    try:
        await _admin(app_client)
        spy.calls.clear()
        assert (await app_client.get("/api/v1/academics/terms")).status_code == 200
    finally:
        event.remove(test_engine, "before_cursor_execute", database._count_query)
    slow = [kw for event, kw in spy.calls if event == "slow_request"]
    assert slow and slow[-1]["path"] == "/api/v1/academics/terms" and slow[-1]["db_queries"] >= 1 and slow[-1]["ms"] >= 0
    # and nothing is logged when the request is fast enough
    monkeypatch.setattr(settings, "slow_request_ms", 60000)
    spy.calls.clear()
    await app_client.get("/api/v1/academics/terms")
    assert not [1 for event, _ in spy.calls if event == "slow_request"]


async def test_health_reports_how_fast_the_database_and_redis_answer(app_client):
    body = (await app_client.get("/health/ready")).json()
    assert isinstance(body["database_ms"], int) and isinstance(body["redis_ms"], int) and body["hints"] == []


async def test_a_slow_database_gets_a_plain_hint_about_localhost(app_client, monkeypatch):
    async def slow_db():
        import asyncio

        await asyncio.sleep(0.25)
        return True

    monkeypatch.setattr(main_module, "check_db_connection", slow_db)
    body = (await app_client.get("/health/ready")).json()
    assert body["database_ms"] >= 200 and "127.0.0.1" in " ".join(body["hints"])


async def test_the_database_version_check_does_not_re_read_the_migration_files_on_every_request(app_client, monkeypatch):
    """Regression: when the database was not built by Alembic, the check ran (and parsed every migration file) on EVERY request."""
    monkeypatch.setattr(settings, "check_migrations", True)
    guard.reset_cache()
    calls = {"n": 0}

    async def unknown():
        calls["n"] += 1
        return {"state": "unknown", "missing": []}

    monkeypatch.setattr(guard, "migration_state", unknown)
    for _ in range(15):
        assert (await app_client.get("/health/live")).status_code == 200
        assert (await app_client.get("/")).status_code == 200
    assert calls["n"] == 1                                              # looked once, then left alone for a minute
    guard.code_heads.cache_clear()
    guard.code_heads()
    guard.code_heads()
    assert guard.code_heads.cache_info().hits >= 1                       # and the files themselves are parsed only once
    guard.reset_cache()