"""topic completion and late attendance marking note

Revision ID: c3f8b6d29a1e
Revises: a2e9c5f18b6d
Create Date: 2026-09-27 00:00:00.000000

Two new fields on class_sessions, both approved in conversation:
topic_completed (teacher marks alongside attendance -- feeds
CourseProgressService's on-time/delayed computation) and
late_marking_note (set only when admin marks attendance outside the
15-minute teacher window -- its mere presence is the "marked late by
admin" flag, no separate boolean needed).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3f8b6d29a1e'
down_revision = 'a2e9c5f18b6d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('class_sessions', sa.Column('topic_completed', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column('class_sessions', 'topic_completed', server_default=None)
    op.add_column('class_sessions', sa.Column('late_marking_note', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('class_sessions', 'late_marking_note')
    op.drop_column('class_sessions', 'topic_completed')