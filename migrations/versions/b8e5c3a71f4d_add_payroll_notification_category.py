"""add PAYROLL notification category

Revision ID: b8e5c3a71f4d
Revises: a7d4f9c62e8b
Create Date: 2026-09-18 00:00:00.000000

Closes a notification-coverage gap: payroll processing (marking a
period PAID) never notified any employee their salary was processed —
there wasn't even a category for it. Same proven pattern as every
other enum-value addition in this project (ALTER TYPE ... ADD VALUE,
no autocommit_block needed in this environment).
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b8e5c3a71f4d'
down_revision = 'a7d4f9c62e8b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE notification_category ADD VALUE IF NOT EXISTS 'PAYROLL'")


def downgrade() -> None:
    # Not removing the enum value — Postgres has no ALTER TYPE ... DROP
    # VALUE, and any notification already created with this category
    # would become unreadable if we tried to work around that. Same
    # one-way-change class as every other enum addition in this project.
    pass