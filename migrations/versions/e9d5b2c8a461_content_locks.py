"""admin content locks on course offerings and class sessions

Revision ID: e9d5b2c8a461
Revises: d7b3f9a52c18
"""
from alembic import op
import sqlalchemy as sa

revision = "e9d5b2c8a461"
down_revision = "d7b3f9a52c18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("course_offerings", sa.Column("content_locked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("class_sessions", sa.Column("content_locked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("class_sessions", "content_locked_at")
    op.drop_column("course_offerings", "content_locked_at")