"""student email, blood group, admission session/category

Revision ID: f9d2a6c41e7b
Revises: e8c3b5a1f492
"""
from alembic import op
import sqlalchemy as sa

revision = "f9d2a6c41e7b"
down_revision = "e8c3b5a1f492"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("students", sa.Column("email", sa.String(255), nullable=True))
    op.add_column("students", sa.Column("blood_group", sa.String(5), nullable=True))
    op.add_column("students", sa.Column("admission_session", sa.String(50), nullable=True))
    op.add_column("students", sa.Column("admission_category", sa.String(50), nullable=True))


def downgrade() -> None:
    for c in ("admission_category", "admission_session", "blood_group", "email"):
        op.drop_column("students", c)