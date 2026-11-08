"""Version and release notes.

* app/releases.json  - the release notes, newest first, written in plain words. Its first entry is the version.
                       Add an entry there with every change that people will notice (see docs/releases.md).
* app/build_info.json - written by the deploy job (GitHub Actions) just before the code is copied to the server:
                       which commit is running and when it was built. Missing on a developer's machine.
"""
from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).resolve().parents[1]
RELEASES_FILE = _HERE / "releases.json"
BUILD_FILE = _HERE / "build_info.json"
KINDS = ("new", "improved", "fixed", "security")


@lru_cache(maxsize=1)
def releases() -> list[dict]:
    try:
        data = json.loads(RELEASES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


@lru_cache(maxsize=1)
def build() -> dict:
    try:
        data = json.loads(BUILD_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def current_version() -> str:
    r = releases()
    return r[0]["version"] if r else "0.0.0"


def _nice_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.strftime("%-d %b %Y")


def version_info() -> dict:
    """Safe for anyone to see: no release notes, no commit message."""
    r = releases()
    b = build()
    commit = str(b.get("commit") or "")[:7]
    return {
        "version": current_version(),
        "released_on": r[0].get("date") if r else None,
        "released_on_text": _nice_date(r[0].get("date")) if r else None,
        "build": commit or None,
        "updated_at": b.get("built_at"),
        "updated_on_text": _nice_date(b.get("built_at")),
    }


def release_notes(limit: int | None = None) -> list[dict]:
    items = releases()
    out = []
    for r in items[: limit or len(items)]:
        out.append({**r, "date_text": _nice_date(r.get("date")), "changes": [c for c in r.get("changes", []) if c.get("kind") in KINDS]})
    return out
