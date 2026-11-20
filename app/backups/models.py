"""Backups seen from the admin panel (app/backups/router.py).

The web app has no access to the backup account, on purpose: a hacked admin account must not be able to delete the
backups. The server writes what happened into these tables (scripts/server/backup.sh, scripts/server/sites.sh jobs),
and carries out the admins' requests (back up now, prepare a download) that the app puts in server_jobs.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class BackupRun(UUIDPKMixin, TimestampMixin, Base):
    """One backup or restore test, recorded by the server."""

    __tablename__ = "backup_runs"
    __table_args__ = (Index("ix_backup_runs_finished_at", "finished_at"),)

    kind: Mapped[str] = mapped_column(String(12), nullable=False)           # NIGHTLY, MANUAL, VERIFY
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    db_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)          # this database copy
    files_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)       # uploaded files on the server
    remote_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)      # everything kept on the backup account
    remote_objects: Mapped[int | None] = mapped_column(Integer, nullable=True)
    copies: Mapped[list | None] = mapped_column(JSONB, nullable=True)                # [{folder, name, bytes, at}] database copies kept


class ServerJob(UUIDPKMixin, TimestampMixin, Base):
    """Something an admin asked for that only the server can do. Picked up within a minute by sites.sh jobs."""

    __tablename__ = "server_jobs"
    __table_args__ = (Index("ix_server_jobs_status", "status"),)

    kind: Mapped[str] = mapped_column(String(16), nullable=False)          # BACKUP_NOW, EXPORT_DB, EXPORT_FILES
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="PENDING", server_default="PENDING")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(120), nullable=True)       # in <storage>/exports/
    file_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
