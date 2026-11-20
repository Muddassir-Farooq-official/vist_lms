"""The admin panel's Backups page: what was backed up and when, how much is kept on the backup account, the monthly
restore test, and three requests the server carries out (scripts/server/sites.sh jobs, within a minute):

  * Back up now                    -> the same backup as the nightly one
  * Download the whole database    -> one .dump file (opens in pgAdmin: Restore), ready for 1 hour
  * Download the uploaded files    -> one ZIP, ready for 1 hour

Administrators only. A download is the whole institute's data, so asking for one needs the password again (and the
6-digit code when two-step sign-in is on); every other administrator is told, both when it is asked for and when it is
downloaded; only the administrator who asked can download it, once ready, within the hour; everything is in the
activity log. The server deletes the file after the hour.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.audit.service import AuditService
from app.auth.models import Role, User, UserRole
from app.auth.permissions import Roles
from app.auth.schemas import CurrentUser
from app.backups.models import BackupRun, ServerJob
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_any_role
from app.core.exceptions import (AuthenticationError, AuthorizationError, DuplicateResourceError, ResourceNotFoundError,
                                 ValidationAppError)
from app.core.net import client_ip

router = APIRouter(prefix="/backups", tags=["backups"])
admin_only = require_any_role(Roles.ADMIN)

KINDS = ("BACKUP_NOW", "EXPORT_DB", "EXPORT_FILES")
OPEN = ("PENDING", "RUNNING")
HELPER_STALE = timedelta(minutes=5)          # sites.sh jobs runs every minute
VERIFY_STALE = timedelta(days=35)            # the restore test runs on the 1st of each month
LABEL = {"BACKUP_NOW": "Back up now", "EXPORT_DB": "Database download (.dump)", "EXPORT_FILES": "Uploaded files (ZIP)"}


def exports_dir() -> Path:
    return Path(settings.storage_local_path).resolve() / "exports"


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def _run(r: BackupRun) -> dict:
    return {"id": str(r.id), "kind": r.kind, "ok": r.ok, "started_at": _iso(r.started_at), "finished_at": _iso(r.finished_at),
            "detail": r.detail, "db_bytes": r.db_bytes, "files_bytes": r.files_bytes, "remote_bytes": r.remote_bytes}


def _job(j: ServerJob, me: uuid.UUID, names: dict, now: datetime) -> dict:
    ready = j.status == "DONE" and j.file_name is not None and j.expires_at is not None and j.expires_at > now
    return {"id": str(j.id), "kind": j.kind, "label": LABEL.get(j.kind, j.kind), "status": j.status, "detail": j.detail,
            "requested_by": names.get(j.requested_by, "—"), "mine": j.requested_by == me, "created_at": _iso(j.created_at),
            "finished_at": _iso(j.finished_at), "file_bytes": j.file_bytes, "expires_at": _iso(j.expires_at),
            "downloaded_at": _iso(j.downloaded_at), "can_download": ready and j.requested_by == me}


async def _admins(db) -> list[User]:
    q = (select(User).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id)
         .where(Role.code == Roles.ADMIN, User.is_active.is_(True)).distinct())
    return list((await db.execute(q)).scalars().all())


async def _tell_other_admins(db, me: uuid.UUID, title: str, message: str) -> None:
    from app.notifications.models import NotificationCategory
    from app.notifications.service import create_notification

    for a in await _admins(db):
        if a.id != me:
            await create_notification(db, user_id=a.id, category=NotificationCategory.GENERAL, title=title, message=message,
                                      link="/dashboard/admin/backups")


def _not_viewing_as(current_user: CurrentUser) -> None:
    if current_user.viewing_as_by_id is not None:
        raise AuthorizationError("Not while viewing someone else's screens.")


@router.get("/status", summary="Backups: history, what is kept, restore test, requests")
async def status(current_user: CurrentUser = Depends(admin_only), db=Depends(get_db)) -> dict:
    from app.core.background import backup_status
    from app.notifications.email_models import ServiceHeartbeat

    now = datetime.now(timezone.utc)
    runs = list((await db.execute(select(BackupRun).order_by(BackupRun.finished_at.desc()).limit(40))).scalars().all())
    last_ok = next((r for r in runs if r.ok and r.kind != "VERIFY"), None)
    if last_ok is None:
        last_ok = (await db.execute(select(BackupRun).where(BackupRun.ok.is_(True), BackupRun.kind != "VERIFY")
                                    .order_by(BackupRun.finished_at.desc()).limit(1))).scalar_one_or_none()
    # what is kept on the backup account, as seen by the newest successful backup that listed it
    stored = (await db.execute(select(BackupRun).where(BackupRun.ok.is_(True), BackupRun.remote_bytes.is_not(None))
                               .order_by(BackupRun.finished_at.desc()).limit(1))).scalar_one_or_none()
    verify = (await db.execute(select(BackupRun).where(BackupRun.kind == "VERIFY").order_by(BackupRun.finished_at.desc())
                               .limit(1))).scalar_one_or_none()
    jobs = list((await db.execute(select(ServerJob).order_by(ServerJob.created_at.desc()).limit(20))).scalars().all())
    ids = {j.requested_by for j in jobs if j.requested_by}
    names = {}
    if ids:
        names = {u.id: u.username for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()}
    helper = await db.get(ServiceHeartbeat, "server-jobs")
    # while the helper is busy with a long request it does not check in; that is not a problem
    helper_ok = (helper is not None and now - helper.last_seen_at < HELPER_STALE) or any(j.status == "RUNNING" for j in jobs)
    counts = dict((await db.execute(select(BackupRun.ok, func.count()).where(BackupRun.kind != "VERIFY",
                                                                            BackupRun.finished_at > now - timedelta(days=30))
                                    .group_by(BackupRun.ok))).all())
    return {
        "now": _iso(now),
        "health": await backup_status(db),
        "last_ok": _run(last_ok) if last_ok else None,
        "stored": None if stored is None else {
            "bytes": stored.remote_bytes, "objects": stored.remote_objects, "as_of": _iso(stored.finished_at),
            "copies": stored.copies or [],
            "daily": sum(1 for c in (stored.copies or []) if c.get("folder") == "daily"),
            "weekly": sum(1 for c in (stored.copies or []) if c.get("folder") == "weekly")},
        "verify": None if verify is None else {**_run(verify), "overdue": now - verify.finished_at > VERIFY_STALE},
        "last_30_days": {"ok": counts.get(True, 0), "failed": counts.get(False, 0)},
        "runs": [_run(r) for r in runs],
        "jobs": [_job(j, current_user.id, names, now) for j in jobs],
        "busy": any(j.status in OPEN for j in jobs),
        "two_factor": bool(getattr(await db.get(User, current_user.id), "totp_secret_enc", None)),
        "helper": {"ok": helper_ok, "last_seen_at": _iso(helper.last_seen_at) if helper else None},
    }


async def _open_job(db, kind: str) -> ServerJob | None:
    return (await db.execute(select(ServerJob).where(ServerJob.kind == kind, ServerJob.status.in_(OPEN)).limit(1))).scalar_one_or_none()


@router.post("/backup-now", summary="Ask the server for a backup now (same as the nightly one)")
async def backup_now(request: Request, current_user: CurrentUser = Depends(admin_only), db=Depends(get_db)) -> dict:
    _not_viewing_as(current_user)
    if await _open_job(db, "BACKUP_NOW"):
        raise DuplicateResourceError("A backup is already waiting or running. It will show below when it finishes.")
    job = ServerJob(kind="BACKUP_NOW", status="PENDING", requested_by=current_user.id)
    db.add(job)
    await db.flush()
    await AuditService(db).log(actor_user_id=current_user.id, action="backups.backup_requested", entity_type="server_job",
                               entity_id=job.id, ip=client_ip(request), user_agent=request.headers.get("user-agent"))
    await db.commit()
    return {"id": str(job.id), "status": job.status}


class ExportIn(BaseModel):
    kind: str = Field(pattern="^(DB|FILES)$")
    password: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=12)


@router.post("/exports", summary="Ask for a complete download: the database (.dump) or the uploaded files (ZIP)")
async def request_export(payload: ExportIn, request: Request, current_user: CurrentUser = Depends(admin_only), db=Depends(get_db)) -> dict:
    from app.auth import two_factor as tf
    from app.auth.service import AuthService

    _not_viewing_as(current_user)
    ip, ua = client_ip(request), request.headers.get("user-agent")
    # the password again (rate-limited and logged like a sign-in), then the 6-digit code if two-step sign-in is on.
    # A wrong answer is a 422, not a 401: the page must not take it for an ended session.
    try:
        user = await AuthService(db).check_password(username=current_user.username, password=payload.password, ip=ip, user_agent=ua)
        if user.id != current_user.id:
            raise AuthenticationError("Password is not correct.")
        secret = tf.active_secret(user)
        if secret:
            if not payload.code:
                raise ValidationAppError("Type the 6-digit code from your authenticator app.")
            await tf.check_code(user, secret, payload.code.strip().replace(" ", ""))
    except AuthenticationError as e:
        raise ValidationAppError(e.message) from None
    kind = "EXPORT_DB" if payload.kind == "DB" else "EXPORT_FILES"
    if await _open_job(db, kind):
        raise DuplicateResourceError("That download is already being prepared. It will show below within a few minutes.")
    job = ServerJob(kind=kind, status="PENDING", requested_by=current_user.id)
    db.add(job)
    await db.flush()
    what = "the whole database" if kind == "EXPORT_DB" else "all uploaded files"
    await AuditService(db).log(actor_user_id=current_user.id, action="backups.export_requested", entity_type="server_job",
                               entity_id=job.id, metadata={"kind": kind}, ip=ip, user_agent=ua)
    await _tell_other_admins(db, current_user.id, "Backup download requested",
                             f"{current_user.username} asked for a download of {what} (Backups page). If this was not expected, "
                             "change that account's password and check the activity log.")
    await db.commit()
    return {"id": str(job.id), "status": job.status}


@router.get("/exports/{job_id}/download", summary="Download a prepared file (only the administrator who asked, within 1 hour)")
async def download(job_id: uuid.UUID, request: Request, current_user: CurrentUser = Depends(admin_only), db=Depends(get_db)):
    _not_viewing_as(current_user)
    job = await db.get(ServerJob, job_id)
    now = datetime.now(timezone.utc)
    if job is None or job.kind not in ("EXPORT_DB", "EXPORT_FILES") or job.requested_by != current_user.id:
        raise ResourceNotFoundError("Download not found.")
    if job.status != "DONE" or not job.file_name or job.expires_at is None or job.expires_at <= now:
        raise ResourceNotFoundError("This download is not ready or has expired (each one is kept for 1 hour). Ask for a new one.")
    base = exports_dir()
    path = (base / job.file_name).resolve()
    if path.parent != base or not path.is_file():
        raise ResourceNotFoundError("The file is no longer on the server. Ask for a new one.")
    first = job.downloaded_at is None
    job.downloaded_at = now
    await AuditService(db).log(actor_user_id=current_user.id, action="backups.export_downloaded", entity_type="server_job",
                               entity_id=job.id, metadata={"kind": job.kind, "bytes": job.file_bytes},
                               ip=client_ip(request), user_agent=request.headers.get("user-agent"))
    if first:
        what = "the whole database" if job.kind == "EXPORT_DB" else "all uploaded files"
        await _tell_other_admins(db, current_user.id, "Backup downloaded", f"{current_user.username} downloaded {what} (Backups page).")
    await db.commit()
    media = "application/octet-stream" if job.kind == "EXPORT_DB" else "application/zip"
    return FileResponse(os.fspath(path), media_type=media, filename=job.file_name,
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
