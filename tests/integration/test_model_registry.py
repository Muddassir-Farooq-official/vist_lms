"""Background processes (import worker, SMS / email sender) start with only their own code loaded. Before they save
anything every table must be known, or SQLAlchemy stops with NoReferencedTableError (this broke student imports on the
server: fee_assignments.enrollment_id -> "enrollments")."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CHECK = """
import app.imports.tasks                      # what `rq worker` imports, nothing more
from app.core.database import Base
from app.core.model_registry import load_all_models
load_all_models()
missing = []
for t in Base.metadata.tables.values():
    for fk in t.foreign_keys:
        try:
            fk.column
        except Exception:
            missing.append(f"{t.name}.{fk.parent.name} -> {fk.target_fullname}")
print("MISSING", missing)
print("TABLES", len(Base.metadata.tables))
"""

FULL = """
import app.main
from app.core.database import Base
print("TABLES", len(Base.metadata.tables))
"""


def _run(code: str) -> str:
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout


def test_a_worker_process_knows_every_table_and_every_foreign_key_resolves():
    worker = _run(CHECK)
    assert "MISSING []" in worker, worker
    full = _run(FULL)
    worker_tables = int(worker.split("TABLES")[-1].split()[0])
    app_tables = int(full.split("TABLES")[-1].split()[0])
    assert worker_tables >= app_tables, (worker_tables, app_tables)


def test_the_import_job_and_the_sender_load_every_model_first():
    assert "load_all_models()" in (ROOT / "app/imports/tasks.py").read_text()
    assert "load_all_models()" in (ROOT / "scripts/sms_sender.py").read_text()