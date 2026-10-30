"""Load every database model, so SQLAlchemy knows every table.

The web app imports all routers and therefore all models. A separate process (the import worker `rq worker`, the
SMS / email sender) imports only what its own code needs. When that code then saves a row whose table has a foreign
key to a table nobody imported, SQLAlchemy stops with NoReferencedTableError (for example: importing students creates
fee assignments, whose enrollment_id points at "enrollments"). Calling load_all_models() first prevents that.

Every module under app/ that declares a table (`__tablename__`) is imported, whatever its file is called and whether or
not its folder has an __init__.py, so a new model is never missed.
"""
from __future__ import annotations

import importlib
from pathlib import Path

_done = False


def load_all_models() -> int:
    """Import every app module that declares a table. Returns how many. Safe to call repeatedly (cheap after the first)."""
    global _done
    root = Path(__file__).resolve().parents[1]                          # .../app
    count = 0
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            if "__tablename__" not in path.read_text(encoding="utf-8", errors="ignore"):
                continue
        except OSError:
            continue
        module = ".".join(("app", *path.relative_to(root).with_suffix("").parts))
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        if module == __name__:
            continue
        importlib.import_module(module)
        count += 1
    _done = True
    return count