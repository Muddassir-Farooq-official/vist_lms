"""Single source of truth for "what time/date is it at the institute".

The app previously called date.today() / datetime.now() in ~20 places,
which use the SERVER's timezone. A container defaults to UTC, so on a
Pakistani campus the "today" boundary, the attendance marking window
(15 min from class start) and library due dates were all off by 5 hours.
Everything now goes through here and follows settings.institute_timezone.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.institute_timezone)


def institute_now() -> datetime:
    """Naive wall-clock datetime in the institute's timezone (class times
    are stored as naive wall-clock values, so comparisons stay naive)."""
    return datetime.now(_tz()).replace(tzinfo=None)


def institute_today() -> date:
    return datetime.now(_tz()).date()

def local_date(value: datetime) -> date:
    """The institute's calendar date for a moment in time (timezone-aware values are converted; naive ones are taken as local)."""
    if value.tzinfo is not None:
        return value.astimezone(_tz()).date()
    return value.date()
