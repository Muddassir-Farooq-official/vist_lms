"""complaints can be filed anonymously

Revision ID: a3c7e1f9d052
Revises: e9d5b2c8a461
"""
from alembic import op
import sqlalchemy as sa

revision = "a3c7e1f9d052"
down_revision = "e9d5b2c8a461"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("complaints", sa.Column("is_anonymous", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("complaints", "is_anonymous")