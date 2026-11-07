"""Sick leave carries a medical note (PDF or photo).

Revision ID: e9a1b3c5d7f9
Revises: d8f0a2b4c6e8
Create Date: 2026-11-07 09:00:00.000000

ADDITIVE and safe to run while the old version of the app is still serving:
- three new, empty, nullable columns on leave_requests (note_key, note_name, note_type).
No existing row is changed. Downgrade drops the columns (the stored note files stay on disk).
"""
from alembic import op
import sqlalchemy as sa

revision = "e9a1b3c5d7f9"
down_revision = "d8f0a2b4c6e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leave_requests", sa.Column("note_key", sa.String(length=300), nullable=True))
    op.add_column("leave_requests", sa.Column("note_name", sa.String(length=255), nullable=True))
    op.add_column("leave_requests", sa.Column("note_type", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("leave_requests", "note_type")
    op.drop_column("leave_requests", "note_name")
    op.drop_column("leave_requests", "note_key")
