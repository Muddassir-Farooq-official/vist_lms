"""A payslip that was held back and then paid on its own remembers the day it was paid.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payslips", sa.Column("released_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("payslips", "released_on")
