"""fees: restore the DEFAULT 0 on fee_ledger_entries.late_fee_charged_amount

Revision e5b7d3a91c6f dropped the server default after back-filling. The model
still relies on it, so on any database built through migrations every fee
activation failed with a NOT NULL violation (HTTP 500).

Revision ID: f1a2b3c4d5e6
Revises: e1a6c4b8d739
"""
from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "e1a6c4b8d739"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE fee_ledger_entries SET late_fee_charged_amount = 0 WHERE late_fee_charged_amount IS NULL")
    op.alter_column("fee_ledger_entries", "late_fee_charged_amount", server_default="0")


def downgrade() -> None:
    op.alter_column("fee_ledger_entries", "late_fee_charged_amount", server_default=None)