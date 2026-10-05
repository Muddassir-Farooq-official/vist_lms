"""When the database is behind the code, say so plainly (and stop the moment it is fixed)."""
from __future__ import annotations

import pytest

from app.core import migration_guard as guard
from app.core.config import settings

pytestmark = pytest.mark.asyncio


@pytest.fixture
def guard_on(monkeypatch):
    monkeypatch.setattr(settings, "check_migrations", True)
    guard.reset_cache()
    yield
    guard.reset_cache()


def _fake_state(monkeypatch, states):
    calls = {"n": 0}

    async def fake():
        i = min(calls["n"], len(states) - 1)
        calls["n"] += 1
        return states[i]

    monkeypatch.setattr(guard, "migration_state", fake)
    return calls


BEHIND = {"state": "behind", "missing": ["e9d5b2c8a461"]}
CURRENT = {"state": "current", "missing": []}


async def test_behind_database_gets_a_clear_message_not_a_crash(app_client, guard_on, monkeypatch):
    _fake_state(monkeypatch, [BEHIND])
    api = await app_client.get("/api/v1/academics/terms")
    assert api.status_code == 503
    err = api.json()["error"]
    assert err["code"] == "DATABASE_OUT_OF_DATE" and "alembic upgrade head" in err["message"]
    assert err["details"]["pending"] == ["e9d5b2c8a461"]

    page = await app_client.get("/dashboard/admin/timetable")
    assert page.status_code == 503 and "alembic upgrade head" in page.text and "e9d5b2c8a461" in page.text

    # the parts that need no database keep working, and health tells the truth
    assert (await app_client.get("/health/live")).status_code == 200
    assert (await app_client.get("/static/css/theme.css")).status_code == 200
    ready = await app_client.get("/health/ready")
    assert ready.status_code == 503 and ready.json()["migrations"] == "behind"


async def test_it_recovers_by_itself_once_the_database_is_updated(app_client, guard_on, monkeypatch):
    calls = _fake_state(monkeypatch, [BEHIND, CURRENT])
    assert (await app_client.get("/")).status_code == 503
    assert (await app_client.get("/")).status_code == 503            # still behind: no needless re-check within a moment
    assert calls["n"] == 1
    guard._cache["checked_at"] = 0.0                                   # a few seconds later...
    assert (await app_client.get("/")).status_code == 200              # ...the admin has run the update: back to normal, no restart
    for _ in range(3):
        assert (await app_client.get("/")).status_code == 200
    assert calls["n"] == 2                                             # and once current it is never checked again


async def test_a_database_not_built_by_alembic_is_left_alone(app_client, guard_on, monkeypatch):
    _fake_state(monkeypatch, [{"state": "unknown", "missing": []}])
    assert (await app_client.get("/")).status_code == 200


async def test_the_check_can_be_switched_off(app_client, monkeypatch):
    monkeypatch.setattr(settings, "check_migrations", False)
    guard.reset_cache()
    _fake_state(monkeypatch, [BEHIND])
    assert (await app_client.get("/")).status_code == 200


async def test_the_code_has_exactly_one_migration_head():
    """Two heads would make `alembic upgrade head` fail; catch it before it ships."""
    assert len(guard.code_heads()) == 1


# ====================================================================== missing tables (the update files were never installed)
def test_the_missing_tables_are_worked_out_from_what_the_code_needs():
    assert guard.tables_missing({"tasks", "gate_logs", "students"}, {"students", "users"}) == ["gate_logs", "tasks"]
    assert guard.tables_missing({"a"}, {"a", "b"}) == []


async def test_a_database_that_claims_to_be_current_but_lacks_tables_says_so_plainly(app_client, guard_on, monkeypatch):
    _fake_state(monkeypatch, [{"state": "incomplete", "missing": ["gate_logs", "tasks"]}])
    r = await app_client.get("/api/v1/gate/summary")
    assert r.status_code == 503
    body = r.json()["error"]
    assert body["code"] == "DATABASE_OUT_OF_DATE" and "gate_logs, tasks" in body["message"] and "migrations/versions" in body["message"] and "alembic upgrade head" in body["message"]
    assert body["details"]["pending"] == ["gate_logs", "tasks"]
    page = await app_client.get("/dashboard/gate")
    assert page.status_code == 503 and "gate_logs" in page.text and "migrations/versions" in page.text and "alembic upgrade head" in page.text


async def test_it_stops_saying_so_once_the_tables_exist(app_client, guard_on, monkeypatch):
    _fake_state(monkeypatch, [{"state": "incomplete", "missing": ["tasks"]}, {"state": "current", "missing": []}])
    assert (await app_client.get("/api/v1/tasks/inbox")).status_code == 503
    guard.reset_cache()
    assert (await app_client.get("/api/v1/tasks/inbox")).status_code != 503


async def test_a_page_that_hits_a_missing_table_gets_the_same_plain_instruction_not_a_vague_error():
    import json

    from sqlalchemy.exc import ProgrammingError
    from starlette.requests import Request

    from app.main import unhandled_exception_handler

    class Missing(Exception):
        def __init__(self, text):
            self.text = text

        def __str__(self) -> str:
            return self.text

    req = Request({"type": "http", "path": "/api/v1/tasks/inbox", "headers": []})
    for text, what in (('relation "tasks" does not exist', "tasks"), ('column "late_fee_charged_amount" of relation "fee_ledger_entries" does not exist', "late_fee_charged_amount")):
        resp = await unhandled_exception_handler(req, ProgrammingError("SELECT 1", {}, Missing(text)))
        body = json.loads(resp.body)["error"]
        assert resp.status_code == 503 and body["code"] == "DATABASE_OUT_OF_DATE" and what in body["message"] and "alembic upgrade head" in body["message"] and "migrations/versions" in body["message"]
    other = await unhandled_exception_handler(req, RuntimeError("something else entirely"))                                       # anything else stays the generic message
    assert other.status_code == 500 and json.loads(other.body)["error"]["message"] == "An unexpected error occurred."