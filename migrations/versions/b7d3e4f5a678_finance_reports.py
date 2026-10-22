"""Finance reports: each employee's salary bank account (for the bank-transfer sheet).

Revision ID: b7d3e4f5a678
Revises: a6c2d3e4f567
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7d3e4f5a678"
down_revision = "a6c2d3e4f567"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("bank_name", sa.String(100), nullable=True))
    op.add_column("employees", sa.Column("bank_account_title", sa.String(150), nullable=True))
    op.add_column("employees", sa.Column("bank_iban", sa.String(34), nullable=True))


def downgrade() -> None:
    op.drop_column("employees", "bank_iban")
    op.drop_column("employees", "bank_account_title")
    op.drop_column("employees", "bank_name")