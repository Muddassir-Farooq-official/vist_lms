"""Announcements written by teachers and staff (approved by the admin); audiences by semester and course section.

Revision ID: b9d1f3a5c7e9
Revises: a8c0e2f4b6d8
Create Date: 2026-11-10 18:22:43.578640

ADDITIVE and safe to run while the old version of the app is still serving: eight new nullable columns on announcements
(no defaults, so no table rewrite), one index and two foreign keys to users (SET NULL). Existing announcements keep NULL
everywhere, which means "written by the admin" and "no semester / section filter": they behave exactly as before.
Downgrade drops the columns: staff announcements still waiting or rejected become plain drafts (still invisible), and
published ones lose their semester / section filter (they would then reach every student of the chosen programs).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b9d1f3a5c7e9'
down_revision = 'a8c0e2f4b6d8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('announcements', sa.Column('semesters', sa.JSON(), nullable=True))
    op.add_column('announcements', sa.Column('offering_ids', sa.JSON(), nullable=True))
    op.add_column('announcements', sa.Column('review', sa.String(length=10), nullable=True))
    op.add_column('announcements', sa.Column('review_note', sa.String(length=500), nullable=True))
    op.add_column('announcements', sa.Column('submitted_by', sa.Uuid(), nullable=True))
    op.add_column('announcements', sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('announcements', sa.Column('reviewed_by', sa.Uuid(), nullable=True))
    op.add_column('announcements', sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_announcements_submitted_by', 'announcements', ['submitted_by'], unique=False)
    op.create_foreign_key(op.f('fk_announcements_submitted_by_users'), 'announcements', 'users', ['submitted_by'], ['id'], ondelete='SET NULL')
    op.create_foreign_key(op.f('fk_announcements_reviewed_by_users'), 'announcements', 'users', ['reviewed_by'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_announcements_reviewed_by_users'), 'announcements', type_='foreignkey')
    op.drop_constraint(op.f('fk_announcements_submitted_by_users'), 'announcements', type_='foreignkey')
    op.drop_index('ix_announcements_submitted_by', table_name='announcements')
    op.drop_column('announcements', 'reviewed_at')
    op.drop_column('announcements', 'reviewed_by')
    op.drop_column('announcements', 'submitted_at')
    op.drop_column('announcements', 'submitted_by')
    op.drop_column('announcements', 'review_note')
    op.drop_column('announcements', 'review')
    op.drop_column('announcements', 'offering_ids')
    op.drop_column('announcements', 'semesters')
