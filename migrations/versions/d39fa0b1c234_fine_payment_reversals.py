"""Fine payment reversals: keeps the cancelled receipt when a fine payment is taken back.

Revision ID: d39fa0b1c234
Revises: a06c7d8e9f01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d39fa0b1c234"
down_revision = "a06c7d8e9f01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fine_payment_reversals",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("fine_id", sa.Uuid(), sa.ForeignKey("fines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("receipt_number", sa.String(30), nullable=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=True),
        sa.Column("payment_method", sa.String(20), nullable=True),
        sa.Column("payment_reference", sa.String(80), nullable=True),
        sa.Column("reversed_on", sa.Date(), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reversed_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason", sa.String(300), nullable=False),
    )
    op.create_index("ix_fine_payment_reversals_fine_id", "fine_payment_reversals", ["fine_id"])


def downgrade() -> None:
    op.drop_index("ix_fine_payment_reversals_fine_id", table_name="fine_payment_reversals")
    op.drop_table("fine_payment_reversals")
