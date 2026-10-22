"""The arithmetic of staff timing. Pure functions (no database), so every edge is easy to test."""
from __future__ import annotations

from datetime import datetime, time

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def parse_days(value: str | None) -> set[int]:
    return {int(x) for x in (value or "").split(",") if x.strip().isdigit() and 0 <= int(x) <= 6}


def format_days(days) -> str:
    return ",".join(str(d) for d in sorted(set(int(x) for x in days)))


def describe_days(days: set[int]) -> str:
    if days == {0, 1, 2, 3, 4}:
        return "Mon–Fri"
    if days == {0, 1, 2, 3, 4, 5}:
        return "Mon–Sat"
    return ", ".join(DAY_NAMES[d] for d in sorted(days)) or "no days"


def _at(moment: datetime, clock: time) -> datetime:
    return moment.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)


def late_minutes(arrived: datetime, expected_in: time | None, grace: int, working_day: bool) -> int:
    """Minutes after the expected arrival time. Arriving within the relaxation counts as on time;
    after it, the lateness is counted from the expected time (the relaxation only forgives small delays)."""
    if not working_day or expected_in is None:
        return 0
    minutes = int((arrived - _at(arrived, expected_in)).total_seconds() // 60)
    return 0 if minutes <= grace else minutes


def early_minutes(left: datetime, expected_out: time | None, grace: int, working_day: bool) -> int:
    """Minutes before the expected leaving time, beyond the relaxation, or 0."""
    if not working_day or expected_out is None:
        return 0
    minutes = int((_at(left, expected_out) - left).total_seconds() // 60)
    return 0 if minutes <= grace else minutes


def marks_to_days(marks: int, per_day: int) -> int:
    """Every `per_day` late arrivals / early leavings together count as one day's pay (4 marks = 1 day, 7 = 1, 8 = 2)."""
    return marks // per_day if per_day > 0 and marks > 0 else 0


def human(minutes: int) -> str:
    h, m = divmod(max(0, minutes), 60)
    return f"{h} h {m} min" if h else f"{m} min"