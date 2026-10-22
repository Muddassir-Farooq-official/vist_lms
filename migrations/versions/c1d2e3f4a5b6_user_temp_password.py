"""Random one-time passwords: keep the handed-over password on the login until the person changes it.

Revision ID: c1d2e3f4a5b6
Revises: e1a6b7c8d901
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "e1a6b7c8d901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("temp_password", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "temp_password")
