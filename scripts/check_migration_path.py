"""Before migrating the REAL database: list the migrations that will run and refuse destructive ones.

Run by scripts/deploy.sh (audit OPS-01 / DB-01). It reads the database's current revision, works out which migrations
`alembic upgrade head` would apply, and looks inside each one. A migration whose upgrade deletes rows or drops tables
or columns stops the deploy, unless that exact revision id is listed in ALLOW_DESTRUCTIVE_MIGRATIONS (comma separated)
after someone has checked it and taken a backup.

    python scripts/check_migration_path.py            # report and check (exit 1 = stop)
"""
from __future__ import annotations

import inspect
import os
import re
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect as sa_inspect, text

ROOT = Path(__file__).resolve().parents[1]
# Removing a permission from a role changes access, not records, and is re-created by seed_permissions.py if needed.
ACCESS_ONLY = re.compile(r"DELETE\s+FROM\s+(role_permissions|permissions)\b", re.IGNORECASE)
DESTRUCTIVE = re.compile(r"\b(drop_table|drop_column|DELETE\s+FROM|TRUNCATE|DROP\s+TABLE|DROP\s+COLUMN)\b", re.IGNORECASE)


def destructive_lines(module) -> list[str]:
    fn = getattr(module, "upgrade", None)
    if fn is None:
        return []
    src = inspect.getsource(fn)
    return [line.strip() for line in src.splitlines() if DESTRUCTIVE.search(line) and not ACCESS_ONLY.search(line) and not line.strip().startswith("#")]


def pending(url: str) -> tuple[list[str], list]:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    engine = create_engine(url)
    with engine.connect() as c:
        current = []
        if sa_inspect(c).has_table("alembic_version"):
            current = [r[0] for r in c.execute(text("SELECT version_num FROM alembic_version"))]
    engine.dispose()
    if not current:
        return [], []
    for rev in current:
        if script.get_revision(rev) is None:
            raise SystemExit(f"STOP: the database is at revision {rev}, which this code does not know. Do not deploy; see the audit (DB-01).")
    heads = script.get_heads()
    todo = list(script.iterate_revisions(heads, current))[::-1]      # oldest first
    return current, [r for r in todo if r.revision not in current]


def main() -> int:
    url = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL", "")
    url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
    if not url:
        print("STOP: DATABASE_URL_SYNC is not set.")
        return 1
    current, todo = pending(url)
    if not current:
        print("Empty database (no alembic_version): a fresh install, every migration will run.")
        return 0
    print(f"Database is at: {', '.join(current)}")
    if not todo:
        print("Up to date: no migrations to run.")
        return 0
    allowed = {x.strip() for x in os.environ.get("ALLOW_DESTRUCTIVE_MIGRATIONS", "").split(",") if x.strip()}
    blocked = []
    print(f"{len(todo)} migration(s) will run:")
    for rev in todo:
        bad = destructive_lines(rev.module)
        flag = "  DESTRUCTIVE" if bad else ""
        print(f"  {rev.revision}  {(rev.doc or '').splitlines()[0][:90]}{flag}")
        for line in bad[:5]:
            print(f"      {line[:110]}")
        if bad and rev.revision not in allowed:
            blocked.append(rev.revision)
    if blocked:
        print("\nSTOP: these migrations delete data or drop tables/columns: " + ", ".join(blocked))
        print("Check them on a restored copy first. To go ahead, take a backup and run the deploy with")
        print(f"  ALLOW_DESTRUCTIVE_MIGRATIONS={','.join(blocked)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
