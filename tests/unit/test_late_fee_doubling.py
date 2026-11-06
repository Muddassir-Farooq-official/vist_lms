"""Late fee that doubles every N late days (Settings -> Rules -> Fees), with an optional highest daily rate."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.core.config import settings
from app.fees.assignment_service import FeeStatusCalculator
from app.fees.models import FeeLedgerEntry, LedgerEntryType

TODAY = date(2026, 9, 16)


def _debit(due: date) -> FeeLedgerEntry:
    return FeeLedgerEntry(id=uuid.uuid4(), entry_type=LedgerEntryType.DEBIT, amount=4000, due_date=due, installment_number=1, late_fee_charged_amount=0)


@pytest.fixture(autouse=True)
def _rules(monkeypatch):
    monkeypatch.setattr(settings, "fee_late_fee_grace_days", 0)
    monkeypatch.setattr(settings, "fee_late_fee_cap", 0)
    monkeypatch.setattr(settings, "fee_late_fee_double_every_days", 0)
    monkeypatch.setattr(settings, "fee_late_fee_max_per_day", 0)


def _row(days_late: int, rate: float = 50):
    return FeeStatusCalculator.installments([_debit(TODAY - timedelta(days=days_late))], rate, as_of=TODAY)[0]


def test_off_by_default_one_flat_rate_as_before():
    r = _row(10)
    assert r["late_fee"] == 500 and r["late_fee_rate_now"] == 50 and r["late_fee_rate_rises_on"] is None


def test_doubles_after_every_week_late(monkeypatch):
    monkeypatch.setattr(settings, "fee_late_fee_double_every_days", 7)
    assert _row(7)["late_fee"] == 7 * 50                                   # first week: Rs. 50 a day
    assert _row(10)["late_fee"] == 7 * 50 + 3 * 100                         # second week: Rs. 100 a day
    assert _row(15)["late_fee"] == 7 * 50 + 7 * 100 + 1 * 200               # third week: Rs. 200 a day
    assert _row(22)["late_fee"] == 7 * 50 + 7 * 100 + 7 * 200 + 1 * 400
    r = _row(10)
    assert r["late_fee_rate_now"] == 100 and r["late_fee_rate_rises_on"] == TODAY + timedelta(days=4)


def test_highest_daily_rate_stops_the_doubling(monkeypatch):
    monkeypatch.setattr(settings, "fee_late_fee_double_every_days", 7)
    monkeypatch.setattr(settings, "fee_late_fee_max_per_day", 120)
    assert _row(15)["late_fee"] == 7 * 50 + 7 * 100 + 1 * 120
    r = _row(15)
    assert r["late_fee_rate_now"] == 120 and r["late_fee_rate_rises_on"] is None   # nothing left to double


def test_total_cap_and_grace_still_apply(monkeypatch):
    monkeypatch.setattr(settings, "fee_late_fee_double_every_days", 7)
    monkeypatch.setattr(settings, "fee_late_fee_cap", 600)
    assert _row(15)["late_fee"] == 600
    monkeypatch.setattr(settings, "fee_late_fee_cap", 0)
    monkeypatch.setattr(settings, "fee_late_fee_grace_days", 3)                     # late days count from the end of the grace
    assert _row(10)["late_fee"] == 7 * 50


def test_a_paid_instalment_keeps_the_fee_for_the_days_it_was_late(monkeypatch):
    monkeypatch.setattr(settings, "fee_late_fee_double_every_days", 7)
    from datetime import datetime, timezone

    d = _debit(TODAY - timedelta(days=20))
    paid = FeeLedgerEntry(id=uuid.uuid4(), entry_type=LedgerEntryType.CREDIT, amount=4000, paid_on=TODAY - timedelta(days=11),
                          created_at=datetime(2026, 9, 5, tzinfo=timezone.utc))
    r = FeeStatusCalculator.installments([d, paid], 50, as_of=TODAY)[0]
    assert r["remaining"] == 0 and r["late_fee"] == 7 * 50 + 2 * 100 and r["late_fee_rate_now"] is None