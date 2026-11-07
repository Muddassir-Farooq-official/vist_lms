"""Server safety helpers: re-encrypting stored secrets for a new JWT_SECRET (scripts/reseal_secrets.py) and the
off-server backup status shown on the admin dashboard (scripts/server/backup.sh records it)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.core import secretbox
from app.core.background import backup_status
from app.core.config import settings
from app.notifications.email_models import EmailSettings, ServiceHeartbeat

pytestmark = pytest.mark.asyncio


async def test_reseal_moves_the_smtp_password_to_the_new_secret_and_back(db_session, monkeypatch):
    from scripts.reseal_secrets import reseal

    old = settings.jwt_secret
    db_session.add(EmailSettings(id=1, host="smtp.example.org", password_enc=secretbox.seal("mail-pass-123")))
    await db_session.commit()
    new = "n" * 64
    assert await reseal(old, new, db_session) == {"re_encrypted": 1, "left_alone": 0}
    assert settings.jwt_secret == old                                            # the running settings are left as they were
    sealed = (await db_session.execute(select(EmailSettings.password_enc).execution_options(populate_existing=True))).scalar_one()
    assert secretbox.open_(sealed) is None                                       # unreadable with the old secret now
    monkeypatch.setattr(settings, "jwt_secret", new)
    assert secretbox.open_(sealed) == "mail-pass-123"
    monkeypatch.setattr(settings, "jwt_secret", old)
    assert await reseal(old, new, db_session) == {"re_encrypted": 0, "left_alone": 1}       # running it twice changes nothing
    assert await reseal(new, old, db_session) == {"re_encrypted": 1, "left_alone": 0}       # --back
    sealed = (await db_session.execute(select(EmailSettings.password_enc).execution_options(populate_existing=True))).scalar_one()
    assert secretbox.open_(sealed) == "mail-pass-123"


async def _beat(session, name, hours_ago, detail):
    session.add(ServiceHeartbeat(name=name, last_seen_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago), detail=detail))
    await session.flush()


async def test_backup_status_on_the_dashboard(db_session, monkeypatch):
    await db_session.execute(delete(ServiceHeartbeat).where(ServiceHeartbeat.name.in_(["backup", "backup-failed"])))
    assert (await backup_status(db_session))["problem"] is None                  # not production: nothing to nag about
    monkeypatch.setattr(settings, "app_env", "production")
    assert "No off-server backup" in (await backup_status(db_session))["problem"]

    await _beat(db_session, "backup", 40, "database 12M + files to vistbackup:")
    st = await backup_status(db_session)
    assert "40 hours ago" in st["problem"] and not st["failed"]

    await _beat(db_session, "backup-failed", 2, "upload of the database copy failed")
    st = await backup_status(db_session)
    assert st["failed"] and "FAILED: upload of the database copy failed" in st["problem"]

    ok = (await db_session.execute(select(ServiceHeartbeat).where(ServiceHeartbeat.name == "backup"))).scalar_one()
    ok.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    await db_session.flush()
    st = await backup_status(db_session)
    assert st["problem"] is None and not st["failed"] and st["detail"].startswith("database 12M")


async def test_system_status_reports_the_backup(app_client, admin_user):
    from tests.integration.test_fees import _login_admin

    await _login_admin(app_client)
    st = (await app_client.get("/api/v1/system/status")).json()
    assert "backup" in st and st["backup"]["problem"] is None                   # tests do not run as production
