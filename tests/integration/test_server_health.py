"""Server health (app/serverhealth + scripts/server/health.py): what counts as a problem, the page, and the alert."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.core.config import settings
from app.notifications.email_models import ServiceHeartbeat
from app.notifications.models import Notification
from app.serverhealth.models import ServerHealth
from app.serverhealth.service import problems
from tests.integration.test_attendance_and_grading import _login_admin

pytestmark = pytest.mark.asyncio
NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _fresh_now():
    """Each test's own "now": a module-level time goes stale when the full suite runs for many minutes before this file."""
    global NOW
    NOW = datetime.now(timezone.utc)


def _row(server=None, sites=None, age=timedelta(seconds=30)):
    base = {"disk_used_pct": 40.0, "disk_free": 10, "disk_total": 20, "memory": {"used_pct": 50.0, "total": 8, "available": 4},
            "load": [0.1, 0.2, 0.3], "cpus": 2, "uptime_s": 100, "memory_high_since": None, "load_high_since": None}
    site = {"name": "vertex", "domain": "lms.example.pk", "enabled": True, "up": True, "database": True,
            "cert_expires": (NOW + timedelta(days=60)).isoformat()}
    return ServerHealth(name="latest", taken_at=NOW - age, data={"server": {**base, **(server or {})}, "sites": sites if sites is not None else [site],
                                                                    "owner": True, "self": "vertex"})


def test_what_counts_as_a_problem():
    assert problems(None) == [] and problems(_row()) == []
    assert "disk is 91% full" in problems(_row({"disk_used_pct": 91.0}))[0]
    # memory / load only after 10 minutes
    assert problems(_row({"memory_high_since": (NOW - timedelta(minutes=4)).isoformat()})) == []
    assert "memory has been above 90%" in problems(_row({"memory_high_since": (NOW - timedelta(minutes=12)).isoformat()}))[0]
    assert "overloaded" in problems(_row({"load_high_since": (NOW - timedelta(minutes=30)).isoformat()}))[0]
    down = {"name": "fims", "domain": "f.example", "enabled": True, "up": False, "error": "Connection refused", "cert_expires": None}
    off = {"name": "old", "enabled": False, "up": False}
    soon = {"name": "kcon", "domain": "k.example", "enabled": True, "up": True, "database": True, "cert_expires": (NOW + timedelta(days=5)).isoformat()}
    found = problems(_row(sites=[down, off, soon]))
    assert any("fims: the site is not answering" in p for p in found) and not any(p.startswith("old") for p in found)
    assert any("kcon: the HTTPS certificate for k.example expires in" in p for p in found)
    nodb = {"name": "x", "enabled": True, "up": True, "database": False}
    assert "cannot reach its database" in problems(_row(sites=[nodb]))[0]
    stale = problems(_row({"disk_used_pct": 99.0}, age=timedelta(minutes=15)))
    assert len(stale) == 1 and "has not reported" in stale[0]


async def test_page_and_alert(app_client, admin_user, db_session, monkeypatch):
    from app.serverhealth.service import MARK, health_alert_round

    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 1000)
    await db_session.execute(delete(ServiceHeartbeat))
    await _login_admin(app_client)
    r = (await app_client.get("/api/v1/server-health")).json()
    assert r["reported"] is False and r["problems"] == []

    db_session.add(_row({"disk_used_pct": 92.5}))
    await db_session.commit()
    r = (await app_client.get("/api/v1/server-health")).json()
    assert r["reported"] and r["data"]["server"]["disk_used_pct"] == 92.5 and "92.5% full" in r["problems"][0]

    assert await health_alert_round(db_session) >= 1
    assert await health_alert_round(db_session) == 0                    # not again today
    n = (await db_session.execute(select(Notification).where(Notification.title == "Server problem"))).scalars().all()
    assert n and "92.5% full" in n[0].message
    row = await db_session.get(ServerHealth, "latest")
    row.data = _row().data
    await db_session.commit()
    assert await health_alert_round(db_session) == 0
    assert not (await db_session.get(ServiceHeartbeat, MARK)).detail       # fixed: the next problem alerts at once


async def test_only_administrators(app_client, admin_user, db_session):
    from tests.integration.test_announcements import _staff
    from tests.integration.test_attendance_and_grading import _login

    await _login_admin(app_client)
    user, pw = await _staff(app_client, db_session, "SHX", "TEACHER", "35202-5550001-1")
    await app_client.post("/api/v1/auth/logout")
    await _login(app_client, user, pw)
    assert (await app_client.get("/api/v1/server-health")).status_code == 403


def test_the_server_script(tmp_path):
    """health.py: every institute gets the shared server and only its own site; vertex gets all."""
    (tmp_path / "v" / "backups").mkdir(parents=True)
    (tmp_path / "v" / "backups" / "backup.log").write_text("2026-11-20 02:31:00  Backup OK: database 4M\n2026-11-21 02:31:00  STOPPED: cannot reach\n")
    (tmp_path / "f").mkdir()
    cfg = {"sites": [{"name": "vertex", "dir": str(tmp_path / "v"), "port": 1, "domain": "", "enabled": True},
                     {"name": "fims", "dir": str(tmp_path / "f"), "port": 2, "domain": "", "enabled": False}],
           "state": str(tmp_path / "state.json"), "cache": str(tmp_path / "cache.json")}
    script = Path(__file__).resolve().parents[2] / "scripts" / "server" / "health.py"
    out = subprocess.run([sys.executable, str(script)], input=json.dumps(cfg).encode(), capture_output=True, timeout=60)
    assert out.returncode == 0, out.stderr
    lines = dict(line.split("\t", 1) for line in out.stdout.decode().splitlines())
    v, f = json.loads(lines["vertex"]), json.loads(lines["fims"])
    assert [s["name"] for s in v["sites"]] == ["vertex", "fims"] and v["owner"] is True
    assert [s["name"] for s in f["sites"]] == ["fims"] and f["owner"] is False
    assert v["sites"][0]["up"] is False and v["sites"][0]["backup_ok_at"] == "2026-11-20 02:31:00"
    assert v["sites"][0]["backup_failed_at"] == "2026-11-21 02:31:00"
    assert f["sites"][0]["error"] == "switched off"
    assert 0 < v["server"]["disk_used_pct"] <= 100 and v["server"]["cpus"] >= 1
