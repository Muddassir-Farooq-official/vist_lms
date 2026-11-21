"""Teacher workload: each teacher's own teaching-load range (credit hours a week).

Production impact: two NULLABLE columns on teacher_availability (a small table added in b5d7f9a1c3e6, one row per teacher
who has set anything). NULL = the institute's range (Settings -> Teaching load, default 12-18). No data changes, no rewrite.
Downgrade drops the two columns (only per-teacher ranges are lost).

Revision ID: c7e9a1b3d5f8
Revises: b5d7f9a1c3e6
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c7e9a1b3d5f8'
down_revision = 'b5d7f9a1c3e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('teacher_availability', sa.Column('load_min', sa.Integer(), nullable=True))
    op.add_column('teacher_availability', sa.Column('load_max', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('teacher_availability', 'load_max')
    op.drop_column('teacher_availability', 'load_min')
