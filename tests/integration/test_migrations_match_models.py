"""The real database is built by Alembic, but the rest of the suite builds its
schema from the models (create_all). This test closes that gap: it migrates an
EMPTY database to head and fails if the result differs from the models in a way
that breaks inserts (missing tables/columns, nullability, or a server default
present on one side only). That class of drift once made every fee activation
fail with a NOT NULL violation on production databases only."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.database import Base

ROOT = Path(__file__).resolve().parents[2]
SCRATCH = "vist_lms_migration_check"


def _swap_db(url: str, name: str) -> str:
    base, _, tail = url.rpartition("/")
    query = ("?" + tail.split("?", 1)[1]) if "?" in tail else ""
    return f"{base}/{name}{query}"


def _relevant(diff) -> bool:
    if isinstance(diff, tuple):
        return diff[0] in ("add_table", "remove_table", "add_column", "remove_column")
    kind, *_rest = diff[0]
    if kind == "modify_nullable":
        return True
    if kind == "modify_default":
        # (..., database default, model default). Dangerous direction only: the
        # model relies on a server default the migrated database does not have,
        # so ORM inserts that omit the column hit NOT NULL. A database default the
        # model does not declare is harmless.
        db_default, model_default = diff[0][-2], diff[0][-1]
        return db_default is None and model_default is not None
    return False


def test_migrated_schema_matches_models():
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    for m in ("audit", "academics", "auth", "attendance", "employees", "notifications", "profiles", "fees",
              "announcements", "applications", "tasks", "gate", "accounts", "purchasing", "payroll",
              "assignments", "complaints", "fines", "imports", "library", "programs", "students", "policy", "parents", "exams", "sms", "supplementary", "admissions"):
        __import__(f"app.{m}.models")
    __import__("app.fees.payment_models")
    __import__("app.fees.refund_models")
    __import__("app.core.sequence_models")
    __import__("app.academics.terms")

    admin = create_engine(_swap_db(settings.database_url_sync, "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH}"'))
        c.execute(text(f'CREATE DATABASE "{SCRATCH}"'))
    try:
        scratch_sync = _swap_db(settings.database_url_sync, SCRATCH)
        with create_engine(scratch_sync).begin() as c:
            c.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        env = dict(os.environ, ALEMBIC_DATABASE_URL=_swap_db(settings.database_url, SCRATCH))
        run = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env,
                             capture_output=True, text=True)
        assert run.returncode == 0, run.stderr[-2000:]

        engine = create_engine(scratch_sync)
        with engine.connect() as c:
            ctx = MigrationContext.configure(c, opts={"compare_server_default": True})
            diffs = [d for d in compare_metadata(ctx, Base.metadata) if _relevant(d)]
        engine.dispose()
        assert not diffs, "Migrations and models disagree:\n" + "\n".join(str(d)[:300] for d in diffs)
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)'))
        admin.dispose()