"""Split the test files into N groups of about equal run time, for parallel CI jobs.

    pytest $(python scripts/ci_shard.py 2 4)      # the 2nd of 4 groups

Every test file lands in exactly one group (checked with --check). Weight of a file = its number of test functions
plus a little for its size, a good stand-in for run time here (each test sets up its own database rows). Files are
handed out heaviest first to the lightest group, so groups stay balanced as tests are added; no list to maintain.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST = re.compile(r"^\s*(async\s+)?def\s+test_", re.MULTILINE)


def files() -> list[Path]:
    return sorted(p for p in (ROOT / "tests").rglob("test_*.py") if "__pycache__" not in p.parts)


def weight(p: Path) -> float:
    text = p.read_text(encoding="utf-8", errors="ignore")
    return len(TEST.findall(text)) + len(text) / 4000


def groups(n: int) -> list[list[Path]]:
    out: list[list[Path]] = [[] for _ in range(n)]
    load = [0.0] * n
    for p in sorted(files(), key=lambda p: (-weight(p), str(p))):
        i = min(range(n), key=lambda k: (load[k], k))
        out[i].append(p)
        load[i] += weight(p)
    return out


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--check":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 4
        seen = [p for g in groups(n) for p in g]
        ok = len(seen) == len(set(seen)) == len(files())
        for i, g in enumerate(groups(n), 1):
            print(f"group {i}: {len(g)} files, weight {sum(weight(p) for p in g):.0f}")
        print("every test file is in exactly one group" if ok else "PROBLEM: groups do not cover the test files exactly once")
        return 0 if ok else 1
    k, n = int(sys.argv[1]), int(sys.argv[2])
    print(" ".join(str(p.relative_to(ROOT)) for p in groups(n)[k - 1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())