"""Make the pictures already on the server smaller (the same way new uploads are made smaller).

Nothing in the database changes: every file keeps its name, place and format; only its size goes down. The original of
every changed file is first copied to a separate folder, so it can be put back. Delete that folder yourself once you
have opened a few pictures and are happy.

On the VPS, from /opt/vist-lms:

    mkdir -p var/storage-originals && chown 1000:1000 var/storage-originals

    # 1. look first (changes nothing)
    docker compose -f docker-compose.prod.yml run --rm --no-deps \
        -v ./var/storage-originals:/app/var/storage-originals app python scripts/shrink_stored_images.py

    # 2. do it
    docker compose -f docker-compose.prod.yml run --rm --no-deps \
        -v ./var/storage-originals:/app/var/storage-originals app python scripts/shrink_stored_images.py --apply

    # 3. after checking some pictures in the app: rm -rf var/storage-originals
    #    to undo instead:                          cp -a var/storage-originals/<date>/. var/storage/

Branding images (logo etc.) are left alone; they are already small.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.storage import max_side_for, shrink_image  # noqa: E402

TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
SKIP_TOP = {"branding"}


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:,.1f} MB"


def run(storage: Path, keep: Path, apply: bool, min_gain: float = 0.10) -> tuple[int, int, int]:
    """Returns (files changed, bytes before, bytes after) for the changed files."""
    changed = before = after = 0
    for path in sorted(storage.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TYPES:
            continue
        rel = path.relative_to(storage)
        if rel.parts[0] in SKIP_TOP:
            continue
        # archive/<stamp>/<category>/... keeps the category one level down
        category = rel.parts[2] if rel.parts[0] == "archive" and len(rel.parts) > 3 else rel.parts[0]
        side = max_side_for(category)
        data = path.read_bytes()
        small = shrink_image(data, TYPES[path.suffix.lower()], side)
        if len(small) > len(data) * (1 - min_gain):
            continue
        changed += 1
        before += len(data)
        after += len(small)
        if apply:
            copy = keep / rel
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, copy)
            tmp = path.with_name(path.name + ".shrinking")
            tmp.write_bytes(small)
            os.replace(tmp, path)
    return changed, before, after


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="really change the files (without it: only report)")
    ap.add_argument("--originals", default="var/storage-originals", help="where the originals are copied first")
    a = ap.parse_args()

    storage = Path(settings.storage_local_path).resolve()
    keep_root = Path(a.originals).resolve()
    if not storage.is_dir():
        sys.exit(f"No storage folder at {storage}")
    if keep_root == storage or storage in keep_root.parents:
        sys.exit("The originals folder must be outside the storage folder (otherwise backups would carry both).")
    if a.apply and not keep_root.is_dir():
        sys.exit(f"{keep_root} is not there. Start the command with:  -v ./var/storage-originals:/app/var/storage-originals")
    keep = keep_root / datetime.now().strftime("%Y%m%d-%H%M%S")

    n, b, s = run(storage, keep, a.apply)
    if not n:
        print("Nothing to make smaller.")
        return
    print(f"{n} pictures: {_mb(b)} -> {_mb(s)}  (saves {_mb(b - s)})")
    if a.apply:
        print(f"Done. Originals are in {keep}. Check some pictures in the app, then delete that folder.")
    else:
        print("Nothing was changed. Run again with --apply to do it.")


if __name__ == "__main__":
    main()
