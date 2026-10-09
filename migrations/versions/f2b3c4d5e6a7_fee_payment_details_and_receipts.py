"""fees: payment method, reference, paid-on date and receipt number on payments

Every recorded payment now says how it was paid, the bank/transaction
reference and the day it was actually paid, and gets a receipt number, so
fee collections can be reconciled against bank statements.

Revision ID: f2b3c4d5e6a7
Revises: f1a2b3c4d5e6
"""
import sqlalchemy as sa
from alembic import op

revision = "f2b3c4d5e6a7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fee_ledger_entries", sa.Column("payment_method", sa.String(20), nullable=True))
    op.add_column("fee_ledger_entries", sa.Column("payment_reference", sa.String(100), nullable=True))
    op.add_column("fee_ledger_entries", sa.Column("paid_on", sa.Date(), nullable=True))
    op.add_column("fee_ledger_entries", sa.Column("receipt_number", sa.String(30), nullable=True))
    op.create_unique_constraint("uq_fee_ledger_entries_receipt_number", "fee_ledger_entries", ["receipt_number"])
    # Existing payments: they were paid on the day they were recorded.
    op.execute("UPDATE fee_ledger_entries SET paid_on = (created_at AT TIME ZONE 'Asia/Karachi')::date WHERE entry_type = 'CREDIT' AND paid_on IS NULL")


def downgrade() -> None:
    op.drop_constraint("uq_fee_ledger_entries_receipt_number", "fee_ledger_entries", type_="unique")
    for col in ("receipt_number", "paid_on", "payment_reference", "payment_method"):
        op.drop_column("fee_ledger_entries", col)