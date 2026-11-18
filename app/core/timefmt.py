"""Times as people read them: 12-hour clock ("9:00 AM", "1:30 PM"). Stored times stay 24-hour (datetime.time)."""
from __future__ import annotations

from datetime import time


def hm12(t: time | str | None) -> str:
    if t is None or t == "":
        return ""
    if isinstance(t, str):
        h, m = (int(x) for x in t.split(":")[:2])
    else:
        h, m = t.hour, t.minute
    return f"{(h - 1) % 12 + 1}:{m:02d} {'AM' if h < 12 else 'PM'}"


def range12(start, end) -> str:
    return f"{hm12(start)}–{hm12(end)}"
