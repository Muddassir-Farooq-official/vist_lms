"""Depreciation arithmetic, kept apart from the database so it can be checked by hand.

Time is counted in months: a full calendar month is 1/12 of a year; a part month counts by its days
(10 days of a 30-day month = 10/30 of a month). So every full month of a straight-line asset gets the same charge.

- Straight line:     yearly charge = (cost - residual value) / useful life in years.
- Reducing balance:  over a stretch of t years, charge = book value at the start x (1 - (1 - rate)^t).
                     For whole years that is exactly "rate % of what is left each year".
- None:              no charge (land).

A charge never takes the book value below the residual value.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Terms:
    method: str                    # STRAIGHT_LINE | REDUCING_BALANCE | NONE
    cost: Decimal
    residual: Decimal
    life_years: Decimal | None
    rate_percent: Decimal | None

    def describe(self) -> str:
        if self.method == "STRAIGHT_LINE":
            return f"Straight line over {self.life_years.normalize():f} years"
        if self.method == "REDUCING_BALANCE":
            return f"Reducing balance at {self.rate_percent.normalize():f}% a year"
        return "Not depreciated"


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def years_between(start: date, end: date) -> Decimal:
    """Length of start..end (both days included) in years, counting months as twelfths."""
    if end < start:
        return Decimal(0)
    months = Decimal(0)
    d = start
    while d <= end:
        last = month_end(d)
        stop = min(last, end)
        days_in_month = last.day
        months += Decimal((stop - d).days + 1) / Decimal(days_in_month)
        d = last + timedelta(days=1)
    return months / Decimal(12)


def charge(terms: Terms, book_value: Decimal, start: date, end: date) -> Decimal:
    """Depreciation for start..end (both included), given the book value at the start."""
    room = money(book_value) - money(terms.residual)
    if room <= 0 or end < start or terms.method == "NONE":
        return Decimal("0.00")
    t = years_between(start, end)
    if terms.method == "STRAIGHT_LINE":
        if not terms.life_years:
            return Decimal("0.00")
        amount = (terms.cost - terms.residual) / terms.life_years * t
    elif terms.method == "REDUCING_BALANCE":
        if not terms.rate_percent:
            return Decimal("0.00")
        keep = (Decimal(1) - terms.rate_percent / Decimal(100))
        amount = book_value * (Decimal(1) - Decimal(str(float(keep) ** float(t))))
    else:
        return Decimal("0.00")
    return max(Decimal("0.00"), min(money(amount), room))


def used_before(terms: Terms, acquired_on: date, start_from: date) -> Decimal:
    """Depreciation already used up by an asset bought on `acquired_on`, up to the day before `start_from`
    (for assets owned before the system: their book value on the go-live date)."""
    return charge(terms, terms.cost, acquired_on, start_from - timedelta(days=1))


def projection(terms: Terms, book_value: Decimal, start: date, years: int = 10) -> list[dict]:
    """The next few years (from `start`), for the asset's page: charge per year and the book value after it."""
    out = []
    bv = money(book_value)
    d = start
    for _ in range(years):
        end = date(d.year + 1, d.month, 1) - timedelta(days=1) if d.day == 1 else d.replace(year=d.year + 1) - timedelta(days=1)
        c = charge(terms, bv, d, end)
        if c <= 0:
            break
        bv -= c
        out.append({"from": d, "to": end, "depreciation": float(c), "book_value_after": float(bv)})
        d = end + timedelta(days=1)
    return out
