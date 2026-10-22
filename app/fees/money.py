"""SQL building blocks for "money that actually moved" in the fee ledger.

The ledger holds charges (DEBIT), concessions and payments (CREDIT), refunds paid back (REFUND) and staff
reversals of a payment (a DEBIT whose description starts with "Payment reversed"). Accounts reports need the cash
view of that: payments in, refunds and reversals out, dated by the day the money moved.
"""
from __future__ import annotations

from sqlalchemy import Date, and_, case, cast, func, literal_column, or_

from app.core.config import settings
from app.fees.models import FeeLedgerEntry, LedgerEntryType

REVERSAL_PREFIX = "Payment reversed"


def money_day():
    """The day the money moved: the stated payment date, else the institute-local date the row was created."""
    # The zone is written into the SQL (not sent as a parameter) so the same expression can appear in SELECT and GROUP BY.
    zone = literal_column("'" + settings.institute_timezone.replace("'", "") + "'")
    return func.coalesce(FeeLedgerEntry.paid_on, cast(func.timezone(zone, FeeLedgerEntry.created_at), Date))


def is_reversal():
    return and_(FeeLedgerEntry.entry_type == LedgerEntryType.DEBIT, FeeLedgerEntry.description.is_not(None), FeeLedgerEntry.description.like(REVERSAL_PREFIX + "%"))


def moves_money():
    return or_(FeeLedgerEntry.entry_type.in_((LedgerEntryType.CREDIT, LedgerEntryType.REFUND)), is_reversal())


def signed_amount():
    """+ for a payment received, - for a refund paid back or a payment reversed."""
    return case((FeeLedgerEntry.entry_type == LedgerEntryType.CREDIT, FeeLedgerEntry.amount), else_=-FeeLedgerEntry.amount)
