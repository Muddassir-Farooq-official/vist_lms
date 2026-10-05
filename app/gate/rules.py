"""The arithmetic of being late. Pure functions, no database, so every edge can be tested exactly."""
from __future__ import annotations

import math
from datetime import datetime, time
from decimal import ROUND_HALF_UP, Decimal

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def parse_days(value: str | None) -> set[int]:
    return {int(x) for x in (value or "").split(",") if x.strip().isdigit() and 0 <= int(x) <= 6}


def format_days(days: set[int] | list[int]) -> str:
    return ",".join(str(d) for d in sorted(set(days)))


def describe_days(days: set[int]) -> str:
    if days == {0, 1, 2, 3, 4}:
        return "Mon\u2013Fri"
    if days == {0, 1, 2, 3, 4, 5}:
        return "Mon\u2013Sat"
    return ", ".join(DAY_NAMES[d] for d in sorted(days)) or "no days"


def _at(moment: datetime, clock: time) -> datetime:
    return moment.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)


def late_minutes(arrived: datetime, expected_in: time | None, grace_minutes: int, working_day: bool) -> int:
    """Whole minutes after the expected time of arrival, or 0.

    Arriving up to (and including) the end of the grace period is on time. Arriving after it is late, and the lateness is
    counted from the EXPECTED time, not from the end of the grace period -- grace only forgives small delays."""
    if not working_day or expected_in is None:
        return 0
    minutes = int((arrived - _at(arrived, expected_in)).total_seconds() // 60)       # whole minutes: 08:40:59 is "10 minutes", 08:41:00 is "11"
    return 0 if minutes <= grace_minutes else minutes


def early_minutes(left: datetime, expected_out: time | None, grace_minutes: int, working_day: bool) -> int:
    """Whole minutes before the expected time of leaving (beyond the grace period), or 0."""
    if not working_day or expected_out is None:
        return 0
    minutes = int((_at(left, expected_out) - left).total_seconds() // 60)
    return 0 if minutes <= grace_minutes else minutes


def late_hours(minutes: int) -> int:
    """Every STARTED hour counts: 1 minute to 60 minutes late is 1 hour, 61 minutes is 2 hours."""
    return math.ceil(minutes / 60) if minutes > 0 else 0


def student_fine(minutes: int, per_hour: float | Decimal, max_per_day: float | Decimal | None) -> Decimal:
    """Hourly rate x started hours, never more than the daily maximum (when the administration set one)."""
    amount = Decimal(str(per_hour)) * late_hours(minutes)
    if max_per_day is not None:
        amount = min(amount, Decimal(str(max_per_day)))
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def human(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h} h {m} min" if h and m else f"{h} h" if h else f"{m} min"