"""Enrollments remember why and when they were withdrawn (attendance, student left, late withdrawal -> shows as W).

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("enrollments", sa.Column("withdrawal_reason", sa.String(40), nullable=True))
    op.add_column("enrollments", sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("enrollments", "withdrawn_at")
    op.drop_column("enrollments", "withdrawal_reason")
