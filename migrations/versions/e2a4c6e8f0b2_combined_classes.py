"""Combined classes: sections of the same course that one teacher teaches together.

Production impact: additive only, safe to run on the live database.
  - one new nullable column course_offerings.combined_group_id (no default, so the table is not rewritten)
    and an index on it. course_offerings is small (one row per course section per term), so building
    the index takes well under a second and only briefly blocks writes to that table.
  - every existing section stays as it is (not combined) until an administrator combines sections.
Nothing existing is changed or deleted.
Downgrade drops the index and the column (sections combined after the upgrade become separate again;
their timetables, enrolments, attendance and marks are untouched).

Revision ID: e2a4c6e8f0b2
Revises: d1f3b5c7e9a1
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2a4c6e8f0b2'
down_revision = 'd1f3b5c7e9a1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('course_offerings', sa.Column('combined_group_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_course_offerings_combined_group_id'), 'course_offerings', ['combined_group_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_course_offerings_combined_group_id'), table_name='course_offerings')
    op.drop_column('course_offerings', 'combined_group_id')
