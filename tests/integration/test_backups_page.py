"""Admin > Backups (app/backups): what the server recorded, the requests it carries out, and the download rules
(password again, only the admin who asked, within the hour, other admins told, logged). Also the backup alert."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from app.audit.models import AuditLog
from app.auth.models import User, UserRole
from app.auth.permissions import Roles
from app.backups.models import BackupRun, ServerJob
from app.core.config import settings
from app.core.security import hash_password
from app.notifications.email_models import ServiceHeartbeat
from app.notifications.models import Notification

pytestmark = pytest.mark.asyncio
PASS = "AdminPass123!"


async def _login(client, username, password):
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text


async def _second_admin(db, roles) -> User:
    u = User(username="admin_two", password_hash=hash_password("OtherPass123!"), must_change_password=False)
    db.add(u)
    await db.flush()
    db.add(UserRole(user_id=u.id, role_id=roles[Roles.ADMIN].id))
    await db.commit()
    return u


def _ago(**kw):
    return datetime.now(timezone.utc) - timedelta(**kw)


async def test_status_shows_what_the_server_recorded(app_client, db_session, admin_user):
    db_session.add_all([
        BackupRun(kind="NIGHTLY", ok=True, finished_at=_ago(hours=30), detail="database 4M", db_bytes=4_000_000, files_bytes=500_000_000,
                  remote_bytes=900_000_000, remote_objects=1200,
                  copies=[{"folder": "daily", "name": "vist-db-a.dump", "bytes": 4_000_000, "at": "2026-11-19T21:30:00Z"},
                          {"folder": "weekly", "name": "vist-db-b.dump", "bytes": 3_900_000, "at": "2026-11-16T21:30:00Z"}]),
        BackupRun(kind="NIGHTLY", ok=False, finished_at=_ago(hours=6), detail="cannot reach backup remote"),
        BackupRun(kind="VERIFY", ok=True, finished_at=_ago(days=19), detail="restored fine"),
    ])
    await db_session.commit()
    await _login(app_client, "admin_test", PASS)
    r = await app_client.get("/api/v1/backups/status")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["last_ok"]["db_bytes"] == 4_000_000 and s["last_ok"]["files_bytes"] == 500_000_000
    assert s["stored"]["bytes"] == 900_000_000 and s["stored"]["daily"] == 1 and s["stored"]["weekly"] == 1
    assert s["verify"]["ok"] and not s["verify"]["overdue"]
    assert s["last_30_days"] == {"ok": 1, "failed": 1}
    assert [x["ok"] for x in s["runs"]] == [False, True, True]
    assert s["helper"]["ok"] is False and s["two_factor"] is False


async def test_only_administrators(app_client, db_session, seeded_roles):
    u = User(username="teacher_x", password_hash=hash_password("TeachPass123!"), must_change_password=False)
    db_session.add(u)
    await db_session.flush()
    db_session.add(UserRole(user_id=u.id, role_id=seeded_roles[Roles.TEACHER].id))
    await db_session.commit()
    await _login(app_client, "teacher_x", "TeachPass123!")
    assert (await app_client.get("/api/v1/backups/status")).status_code == 403
    assert (await app_client.post("/api/v1/backups/backup-now")).status_code == 403
    assert (await app_client.post("/api/v1/backups/exports", json={"kind": "DB", "password": "TeachPass123!"})).status_code == 403


async def test_back_up_now_once_at_a_time(app_client, db_session, admin_user):
    await _login(app_client, "admin_test", PASS)
    r = await app_client.post("/api/v1/backups/backup-now")
    assert r.status_code == 200 and r.json()["status"] == "PENDING"
    assert (await app_client.post("/api/v1/backups/backup-now")).status_code == 409
    job = (await db_session.execute(select(ServerJob))).scalar_one()
    assert job.kind == "BACKUP_NOW" and job.requested_by == admin_user.id


async def test_download_needs_the_password_and_tells_the_other_admins(app_client, db_session, admin_user, seeded_roles, tmp_path, monkeypatch):
    other = await _second_admin(db_session, seeded_roles)
    monkeypatch.setattr(settings, "storage_local_path", str(tmp_path))
    await _login(app_client, "admin_test", PASS)

    wrong = await app_client.post("/api/v1/backups/exports", json={"kind": "DB", "password": "not-it"})
    assert wrong.status_code == 422                       # not 401: the page must not think the session ended
    assert (await db_session.execute(select(func.count()).select_from(ServerJob))).scalar_one() == 0
    assert (await app_client.get("/api/v1/backups/status")).status_code == 200          # still signed in

    r = await app_client.post("/api/v1/backups/exports", json={"kind": "DB", "password": PASS})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    assert (await app_client.post("/api/v1/backups/exports", json={"kind": "DB", "password": PASS})).status_code == 409
    told = (await db_session.execute(select(Notification).where(Notification.user_id == other.id))).scalars().all()
    assert len(told) == 1 and "admin_test" in told[0].message
    assert not (await db_session.execute(select(Notification).where(Notification.user_id == admin_user.id))).scalars().all()
    assert (await db_session.execute(select(AuditLog).where(AuditLog.action == "backups.export_requested"))).scalar_one()

    # not ready yet
    assert (await app_client.get(f"/api/v1/backups/exports/{job_id}/download")).status_code == 404

    # the server made it (what sites.sh jobs does)
    (tmp_path / "exports").mkdir()
    (tmp_path / "exports" / "database-x.dump").write_bytes(b"PGDMP-test")
    job = await db_session.get(ServerJob, job_id)
    job.status, job.file_name, job.file_bytes, job.expires_at = "DONE", "database-x.dump", 10, _ago(minutes=-60)
    await db_session.commit()

    s = (await app_client.get("/api/v1/backups/status")).json()
    assert s["jobs"][0]["can_download"] is True
    d = await app_client.get(f"/api/v1/backups/exports/{job_id}/download")
    assert d.status_code == 200 and d.content == b"PGDMP-test" and "database-x.dump" in d.headers["content-disposition"]
    assert d.headers["cache-control"] == "no-store"
    await db_session.refresh(job)
    assert job.downloaded_at is not None
    assert (await db_session.execute(select(func.count()).select_from(Notification).where(Notification.user_id == other.id))).scalar_one() == 2
    assert (await db_session.execute(select(AuditLog).where(AuditLog.action == "backups.export_downloaded"))).scalar_one()

    # only the administrator who asked
    await app_client.post("/api/v1/auth/logout")
    await _login(app_client, "admin_two", "OtherPass123!")
    assert (await app_client.get(f"/api/v1/backups/exports/{job_id}/download")).status_code == 404
    assert (await app_client.get("/api/v1/backups/status")).json()["jobs"][0]["can_download"] is False

    # and only within the hour
    await app_client.post("/api/v1/auth/logout")
    await _login(app_client, "admin_test", PASS)
    job.expires_at = _ago(minutes=1)
    await db_session.commit()
    assert (await app_client.get(f"/api/v1/backups/exports/{job_id}/download")).status_code == 404


async def test_a_file_name_cannot_leave_the_exports_folder(app_client, db_session, admin_user, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_local_path", str(tmp_path / "storage"))
    (tmp_path / "storage" / "exports").mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("no")
    job = ServerJob(kind="EXPORT_DB", status="DONE", requested_by=admin_user.id, file_name="../../secret.txt", file_bytes=2,
                    expires_at=_ago(minutes=-30))
    db_session.add(job)
    await db_session.commit()
    await _login(app_client, "admin_test", PASS)
    assert (await app_client.get(f"/api/v1/backups/exports/{job.id}/download")).status_code == 404


async def test_backup_alert_once_a_day_and_again_when_it_changes(db_session, admin_user, seeded_roles, monkeypatch):
    from app.backups.alerts import MARK, backup_alert_round

    other = await _second_admin(db_session, seeded_roles)
    await db_session.execute(delete(ServiceHeartbeat))
    monkeypatch.setattr(settings, "app_env", "production")
    db_session.add(ServiceHeartbeat(name="backup", last_seen_at=_ago(hours=27), detail="ok"))
    await db_session.commit()

    assert await backup_alert_round(db_session) == 2                  # 27 hours > 26: both administrators told
    assert await backup_alert_round(db_session) == 0                  # not again the same day
    note = (await db_session.execute(select(Notification).where(Notification.user_id == other.id))).scalar_one()
    assert "27 hours ago" in note.message

    db_session.add(ServiceHeartbeat(name="backup-failed", last_seen_at=_ago(minutes=1), detail="upload failed"))
    await db_session.commit()
    assert await backup_alert_round(db_session) == 2                  # a different problem: told straight away

    ok = await db_session.get(ServiceHeartbeat, "backup")
    ok.last_seen_at = _ago(minutes=0)
    await db_session.commit()
    assert await backup_alert_round(db_session) == 0                  # fixed
    assert not (await db_session.get(ServiceHeartbeat, MARK)).detail

    db_session.add(BackupRun(kind="VERIFY", ok=False, finished_at=_ago(minutes=5), detail="pg_restore failed"))
    await db_session.commit()
    assert await backup_alert_round(db_session) == 2
    last = (await db_session.execute(select(Notification).where(Notification.user_id == other.id)
                                     .order_by(Notification.created_at.desc()).limit(1))).scalar_one()
    assert "restore test FAILED" in last.message


async def test_two_step_code_is_needed_when_it_is_on(app_client, db_session, admin_user):
    from app.auth import two_factor as tf
    from app.core.redis import get_redis

    await _login(app_client, "admin_test", PASS)               # (two-step sign-in is off for sign-in in tests)
    secret = tf.new_secret()
    r = get_redis()
    async for k in r.scan_iter("2fa:*"):
        await r.delete(k)
    tf.enable(admin_user, secret)
    await db_session.commit()
    assert (await app_client.get("/api/v1/backups/status")).json()["two_factor"] is True
    no_code = await app_client.post("/api/v1/backups/exports", json={"kind": "FILES", "password": PASS})
    assert no_code.status_code == 422 and "6-digit code" in no_code.json()["error"]["message"]
    bad = await app_client.post("/api/v1/backups/exports", json={"kind": "FILES", "password": PASS, "code": "000000"})
    assert bad.status_code == 422
    good = await app_client.post("/api/v1/backups/exports", json={"kind": "FILES", "password": PASS, "code": tf.code_now(secret)})
    assert good.status_code == 200, good.text
    assert (await db_session.get(ServerJob, good.json()["id"])).kind == "EXPORT_FILES"
