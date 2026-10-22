"""Approved leave as an attendance status (not counted for or against the student).

Revision ID: f5b1c2d3e456
Revises: e4a0b1c2d345
"""
from __future__ import annotations

from alembic import op

revision = "f5b1c2d3e456"
down_revision = "e4a0b1c2d345"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE attendance_status ADD VALUE IF NOT EXISTS 'LEAVE'")


def downgrade() -> None:
    # PostgreSQL cannot drop an enum value. Turn any leave back into absence so the old code can read it.
    op.execute("UPDATE attendance_records SET status = 'ABSENT' WHERE status = 'LEAVE'")