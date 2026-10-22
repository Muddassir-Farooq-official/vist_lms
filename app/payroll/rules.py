"""The arithmetic of pay. Pure functions, so every edge can be tested exactly."""
from __future__ import annotations

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal


def q2(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def days_in(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def payable_days(hire_date: date | None, month: date) -> int:
    """Days of the month they are employed for: the whole month, or from the day they joined. 0 if they join after it."""
    start, dim = month_start(month), days_in(month)
    if hire_date is None or hire_date <= start:
        return dim
    if hire_date > month_end(month):
        return 0
    return dim - (hire_date - start).days


def prorate(amount, payable: int, dim: int) -> Decimal:
    return q2(Decimal(str(amount)) * payable / dim)


def item_amount(calc: str, value, base) -> Decimal:
    return q2(Decimal(str(base)) * Decimal(str(value)) / 100) if calc == "PERCENT_OF_BASE" else q2(value)


def unpaid_deduction(base, working_days: int, days) -> Decimal:
    """A day's pay is the month's basic salary divided by the working days set for the payroll (e.g. 26 or 22)."""
    return q2(Decimal(str(base)) / working_days * Decimal(str(days)))


def advance_recovery(monthly, balance, available) -> Decimal:
    """What to take back this month: the agreed instalment, never more than is owed, and never more than leaves the person with nothing."""
    return max(Decimal(0), min(q2(monthly), q2(balance), q2(available)))


def marks_to_days(marks: int, per_day: int) -> int:
    """Every `per_day` late arrivals / early leavings together count as one day's pay (4 = 1 day, 7 = 1, 8 = 2)."""
    return marks // per_day if per_day > 0 and marks > 0 else 0