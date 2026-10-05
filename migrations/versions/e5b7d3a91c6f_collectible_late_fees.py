"""collectible late fees — track how much of each installment's late fee has been posted as a real Fine

Revision ID: e5b7d3a91c6f
Revises: d9a1c6e48b3f
Create Date: 2026-09-15 00:00:00.000000

Real logic gap fix, not a new feature: late_fee_per_day has always been
calculated and DISPLAYED (FeeStatusCalculator.compute's accrued_fine)
but never once written as an actual ledger charge — there was no way
to post it, pay it, or waive it. This migration adds the one column
needed to track "how much of this installment's late fee has actually
been posted as a collectible Fine so far" (late_fee_charged_amount),
which FeeAssignmentService.post_late_fee uses to post only the NEW
amount each time it's called (so calling it repeatedly, e.g. weekly,
never double-charges for days already posted).

The actual charge lands in the existing Fines module (issue/pay/waive
lifecycle already built and proven) rather than a second parallel
ledger inside Fees — also resolves the "two different things both
called a fine" confusion flagged in the same review.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5b7d3a91c6f'
down_revision = 'd9a1c6e48b3f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'fee_ledger_entries',
        sa.Column('late_fee_charged_amount', sa.Numeric(10, 2), server_default='0', nullable=False),
    )
    op.alter_column('fee_ledger_entries', 'late_fee_charged_amount', server_default=None)

    # Same combination (add_column + ALTER TYPE ... ADD VALUE in one
    # migration) already proven to work against a real database in
    # a3c6e9f21b4d — matching that exact pattern rather than adding an
    # autocommit_block() this environment hasn't been confirmed to need.
    op.execute("ALTER TYPE fine_source ADD VALUE IF NOT EXISTS 'FEE_LATE_PAYMENT'")


def downgrade() -> None:
    op.drop_column('fee_ledger_entries', 'late_fee_charged_amount')
    # Not removing the enum value — Postgres has no ALTER TYPE ... DROP
    # VALUE, and any Fine already issued with this source would become
    # unreadable if we tried to work around that. Same one-way-change
    # class as every other enum addition in this project.