from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.fees.assignment_service import FeeStatusCalculator
from app.fees.models import FeeLedgerEntry, LedgerEntryType

TODAY = date(2026, 9, 16)


def _debit(amount: float, due_date: date, installment_number: int = 1) -> FeeLedgerEntry:
    # id set explicitly — these entries are never added to a session/
    # flushed, and SQLAlchemy's `default=uuid.uuid4` on the PK column only
    # runs at INSERT time, not at plain object construction.
    return FeeLedgerEntry(
        id=uuid.uuid4(),
        entry_type=LedgerEntryType.DEBIT,
        amount=amount,
        due_date=due_date,
        installment_number=installment_number,
    )


def _credit(amount: float, created_at: datetime) -> FeeLedgerEntry:
    return FeeLedgerEntry(id=uuid.uuid4(), entry_type=LedgerEntryType.CREDIT, amount=amount, created_at=created_at)


def _at(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


class TestNoLedgerActivity:
    def test_no_entries_at_all_is_zero_everywhere(self):
        result = FeeStatusCalculator.compute([], late_fee_per_day=200)
        assert result == {
            "total_charged": 0,
            "total_paid": 0,
            "overdue_principal": 0,
            "accrued_fine": 0,
            "total_due_now": 0,
        }


class TestFullPaymentOnTime:
    def test_single_installment_paid_in_full_before_due_date_has_zero_due(self):
        due = TODAY + timedelta(days=10)
        entries = [_debit(5000, due), _credit(5000, _at(TODAY))]
        result = FeeStatusCalculator.compute(entries, late_fee_per_day=200, as_of=TODAY)
        assert result["total_charged"] == 5000
        assert result["total_paid"] == 5000
        assert result["overdue_principal"] == 0
        assert result["accrued_fine"] == 0
        assert result["total_due_now"] == 0

    def test_multiple_installments_each_paid_separately_on_time(self):
        entries = [
            _debit(3000, TODAY - timedelta(days=30), installment_number=1),
            _credit(3000, _at(TODAY - timedelta(days=30))),
            _debit(3000, TODAY + timedelta(days=30), installment_number=2),
            _credit(3000, _at(TODAY)),
        ]
        result = FeeStatusCalculator.compute(entries, late_fee_per_day=200, as_of=TODAY)
        assert result["total_due_now"] == 0
        assert result["total_paid"] == 6000


class TestUnpaidOverdue:
    def test_unpaid_installment_past_due_date_is_overdue_principal(self):
        due = TODAY - timedelta(days=5)
        entries = [_debit(4000, due)]
        result = FeeStatusCalculator.compute(entries, late_fee_per_day=None, as_of=TODAY)
        assert result["overdue_principal"] == 4000
        assert result["accrued_fine"] == 0  # no late_fee_per_day configured
        assert result["total_due_now"] == 4000

    def test_unpaid_installment_past_due_date_accrues_per_day_late_fee(self):
        due = TODAY - timedelta(days=5)
        entries = [_debit(4000, due)]
        result = FeeStatusCalculator.compute(entries, late_fee_per_day=200, as_of=TODAY)
        assert result["overdue_principal"] == 4000
        assert result["accrued_fine"] == 5 * 200  # 5 days late * 200/day
        assert result["total_due_now"] == 4000 + 1000

    def test_unpaid_installment_not_yet_due_has_no_overdue_and_no_fine(self):
        due = TODAY + timedelta(days=5)
        entries = [_debit(4000, due)]
        result = FeeStatusCalculator.compute(entries, late_fee_per_day=200, as_of=TODAY)
        assert result["overdue_principal"] == 0
        assert result["accrued_fine"] == 0
        assert result["total_due_now"] == 0
        # But it must still count as charged — it's owed, just not yet late.
        assert result["total_charged"] == 4000


class TestPartialPayment:
    def test_partial_payment_leaves_remaining_principal_overdue(self):
        due = TODAY - timedelta(days=3)
        entries = [_debit(4000, due), _credit(1500, _at(TODAY))]
        result = FeeStatusCalculator.compute(entries, late_fee_per_day=None, as_of=TODAY)
        assert result["total_paid"] == 1500
        assert result["overdue_principal"] == 2500

    def test_partial_payment_still_accrues_fine_on_the_unpaid_remainder(self):
        due = TODAY - timedelta(days=3)
        entries = [_debit(4000, due), _credit(1500, _at(TODAY))]
        result = FeeStatusCalculator.compute(entries, late_fee_per_day=200, as_of=TODAY)
        assert result["overdue_principal"] == 2500
        assert result["accrued_fine"] == 3 * 200


class TestFifoAllocationAcrossInstallments:
    """A single payment must settle the OLDEST unpaid installment first,
    not be split evenly or applied to whichever installment happens to be
    checked first."""

    def test_single_large_payment_settles_oldest_installments_first(self):
        entries = [
            _debit(2000, TODAY - timedelta(days=60), installment_number=1),
            _debit(2000, TODAY - timedelta(days=30), installment_number=2),
            _debit(2000, TODAY + timedelta(days=30), installment_number=3),
            # One payment covering exactly the first two installments.
            _credit(4000, _at(TODAY)),
        ]
        result = FeeStatusCalculator.compute(entries, late_fee_per_day=None, as_of=TODAY)
        # Installments 1 and 2 are fully settled by the 4000 payment;
        # installment 3 isn't due yet, so nothing should be overdue.
        assert result["total_paid"] == 4000
        assert result["overdue_principal"] == 0
        assert result["total_due_now"] == 0

    def test_payment_smaller_than_oldest_installment_only_partially_settles_it(self):
        entries = [
            _debit(2000, TODAY - timedelta(days=10), installment_number=1),
            _debit(2000, TODAY - timedelta(days=5), installment_number=2),
            _credit(1000, _at(TODAY)),
        ]
        result = FeeStatusCalculator.compute(entries, late_fee_per_day=None, as_of=TODAY)
        # 1000 covers only part of installment 1; installment 2 is
        # entirely unpaid. Both remaining balances are overdue.
        assert result["overdue_principal"] == 1000 + 2000

    def test_multiple_payments_are_applied_in_created_order_not_amount_order(self):
        entries = [
            _debit(1000, TODAY - timedelta(days=20), installment_number=1),
            _debit(1000, TODAY - timedelta(days=10), installment_number=2),
            # A late-created but larger payment must not "jump the queue"
            # ahead of the earlier smaller one.
            _credit(500, _at(TODAY - timedelta(days=15))),
            _credit(1500, _at(TODAY)),
        ]
        result = FeeStatusCalculator.compute(entries, late_fee_per_day=None, as_of=TODAY)
        assert result["total_paid"] == 2000
        assert result["overdue_principal"] == 0


class TestSettledLatePayment:
    def test_installment_paid_after_its_due_date_still_accrued_a_fine_for_the_days_it_was_late(self):
        """Once fully paid, principal is no longer 'overdue', but the fine
        for the days it WAS late before payment should still be counted —
        otherwise paying late and paying on time would look identical."""
        due = TODAY - timedelta(days=20)
        paid_on = TODAY - timedelta(days=10)  # paid 10 days after due date
        entries = [_debit(3000, due), _credit(3000, _at(paid_on))]
        result = FeeStatusCalculator.compute(entries, late_fee_per_day=200, as_of=TODAY)
        assert result["overdue_principal"] == 0  # fully paid, not currently overdue
        assert result["accrued_fine"] == 10 * 200  # but was late for 10 days before paying
        # ...which staff can still post as a fine ("Post Late Fee"); on its own it no longer keeps
        # the payer out of good standing forever once the instalment itself is paid.
        assert result["total_due_now"] == 0

    def test_installment_paid_exactly_on_due_date_has_no_fine(self):
        due = TODAY - timedelta(days=5)
        entries = [_debit(3000, due), _credit(3000, _at(due))]
        result = FeeStatusCalculator.compute(entries, late_fee_per_day=200, as_of=TODAY)
        assert result["accrued_fine"] == 0


class TestComputeFineForSingleDebit:
    def test_matches_the_per_installment_share_of_the_assignment_wide_fine(self):
        """post_late_fee (used to actually charge a fine) needs to know
        ONE installment's share of the fine, not the whole assignment's —
        this must independently agree with compute()'s total for a
        single-installment assignment."""
        due = TODAY - timedelta(days=4)
        entries = [_debit(1000, due)]
        whole = FeeStatusCalculator.compute(entries, late_fee_per_day=200, as_of=TODAY)
        single = FeeStatusCalculator.compute_fine_for_debit(
            entries, entries[0].id, late_fee_per_day=200, as_of=TODAY
        )
        assert single == whole["accrued_fine"]