"""Tell people clearly when the database has not been updated to match the code.

New versions of the software often add columns. If the code is updated but
`alembic upgrade head` has not been run, every page that touches the missing
column used to crash with a long, cryptic traceback ("column ... does not
exist"). This module notices the mismatch and lets the app answer with one
plain instruction instead -- and stops answering that the moment the database
is brought up to date (no restart needed).
"""
from __future__ import annotations

import time
from functools import lru_cache
from pathlib import Path

from app.core.config import settings
from app.core.database import engine
from app.core.logging import get_logger

logger = get_logger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
FIX_COMMAND = "alembic upgrade head"
RECHECK_SECONDS = 3.0        # while the database is behind, look again this often


@lru_cache(maxsize=1)
def code_heads() -> frozenset[str]:
    """The migration revision(s) this version of the code expects."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    return frozenset(ScriptDirectory.from_config(cfg).get_heads())   # read once: the files cannot change while running


async def database_revisions() -> set[str] | None:
    """The revision(s) the database is at, or None if it was not built by Alembic."""
    try:
        async with engine.connect() as conn:
            result = await conn.exec_driver_sql("SELECT version_num FROM alembic_version")
            return {row[0] for row in result.fetchall()}
    except Exception:
        return None


def tables_missing(expected: set[str], existing: set[str]) -> list[str]:
    """The tables the code needs that the database does not have."""
    return sorted(expected - existing)


async def missing_tables() -> list[str]:
    """Tables the code works with that are not in the database -- e.g. the update files for a new feature were never installed."""
    try:
        from app.core.database import Base

        async with engine.connect() as conn:
            rows = await conn.exec_driver_sql("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
            return tables_missing({t.name for t in Base.metadata.sorted_tables}, {r[0] for r in rows.fetchall()})
    except Exception:
        return []


async def migration_state() -> dict:
    """{'state': 'current' | 'behind' | 'incomplete' | 'unknown', 'missing': [...]}

    'incomplete' = the database claims to be up to date, yet tables the code needs are missing. That happens when the migration
    FILES for newer features were not copied into migrations/versions, so the version numbers agree but the tables were never made."""
    try:
        wanted = code_heads()
    except Exception:
        return {"state": "unknown", "missing": []}
    have = await database_revisions()
    if have is None:
        return {"state": "unknown", "missing": []}
    missing = sorted(wanted - have)
    if missing:
        return {"state": "behind", "missing": missing}
    absent = await missing_tables()
    return {"state": "incomplete", "missing": absent} if absent else {"state": "current", "missing": []}


# Small cache: once the database is current we never look again (zero cost per request).
_cache: dict = {"checked_at": 0.0, "current": False, "missing": [], "skip_until": 0.0, "kind": "revision"}
UNKNOWN_RECHECK_SECONDS = 60.0     # a database not built by Alembic is looked at once a minute, not on every request


def reset_cache() -> None:
    _cache.update(checked_at=0.0, current=False, missing=[], skip_until=0.0, kind="revision")


async def is_behind() -> tuple[bool, list[str]]:
    if not settings.check_migrations or _cache["current"]:
        return False, []
    now = time.monotonic()
    if _cache["checked_at"] and _cache["missing"] and now - _cache["checked_at"] < RECHECK_SECONDS:
        return True, list(_cache["missing"])
    if now < _cache["skip_until"]:
        return False, []
    state = await migration_state()
    _cache["checked_at"] = now
    if state["state"] == "unknown":
        _cache["skip_until"] = now + UNKNOWN_RECHECK_SECONDS
    if state["state"] == "incomplete":
        if _cache["missing"] != state["missing"]:
            logger.error("database_tables_missing", tables=state["missing"], fix=FIX_COMMAND)
            print(
                "\n" + "=" * 72 + "\n  PARTS OF THE DATABASE ARE MISSING: " + ", ".join(state["missing"][:12]) + (" ..." if len(state["missing"]) > 12 else "")
                + "\n  The update files for the newest features were probably not copied into  migrations/versions/ ."
                "\n  Copy EVERY migration file from the downloads there, then run:   " + FIX_COMMAND + "\n" + "=" * 72 + "\n", flush=True,
            )
        _cache["missing"], _cache["kind"] = state["missing"], "tables"
        return True, state["missing"]
    if state["state"] == "behind":
        _cache["kind"] = "revision"
        if _cache["missing"] != state["missing"]:
            logger.error("database_out_of_date", missing_revisions=state["missing"], fix=FIX_COMMAND)
            # Plain, impossible-to-miss note in the server console (the structured log above is easy to overlook).
            print(
                "\n" + "=" * 72 + "\n  THE DATABASE IS OUT OF DATE (pending: " + ", ".join(state["missing"]) + ")"
                "\n  In the project folder run:   " + FIX_COMMAND + "\n  (no restart needed afterwards)\n" + "=" * 72 + "\n",
                flush=True,
            )
        _cache["missing"] = state["missing"]
        return True, state["missing"]
    _cache["missing"] = []
    _cache["current"] = state["state"] == "current"
    return False, []


TABLES_MESSAGE = ("Some parts of the database are missing ({what}). The update files for the newest features were probably not installed. "
                  "Ask whoever manages the server to copy every file from the migrations folder into migrations/versions, then run: " + FIX_COMMAND)


def kind() -> str:
    """'revision' (the database is behind the migration files) or 'tables' (the migration files themselves are missing)."""
    return _cache.get("kind", "revision")


def tables_page(missing: list[str]) -> str:
    return PAGE.format(fix=FIX_COMMAND, missing=", ".join(missing[:12]) + (" ..." if len(missing) > 12 else "")).replace(
        "The software was updated, but the database has not been brought up to date yet.",
        "Parts of the database that newer features need are missing. The update files for those features were probably not copied into migrations/versions.")


PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Update needed &mdash; VIST LMS</title>
<style>body{{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#f4f6fb;margin:0;display:grid;place-items:center;min-height:100vh;padding:16px}}
.c{{background:#fff;border:1px solid #dfe4ef;border-radius:16px;max-width:520px;padding:28px;box-shadow:0 8px 30px rgba(15,31,61,.08)}}
h1{{margin:0 0 8px;font-size:1.3rem;color:#0f1f3d}}p{{color:#4a5670;line-height:1.5}}
code{{display:block;background:#0f1f3d;color:#fff;padding:12px 14px;border-radius:10px;margin:14px 0;font-size:.95rem}}</style></head>
<body><div class="c"><h1>The system needs a quick update</h1>
<p>The software was updated, but the database has not been brought up to date yet. Whoever manages the server should open a terminal in the project folder and run:</p>
<code>{fix}</code><p>Then reload this page &mdash; no restart is needed. (Pending updates: {missing}.)</p></div></body></html>"""