"""lecture room and scheduled start/end time

Revision ID: d1a7e4c95f2b
Revises: c9f6a2e83b5d
Create Date: 2026-09-23 00:00:00.000000

Admin-set, display-only fields shown to the teacher when they select a
lecture to take attendance for — approved decision: these are fixed
properties of the Lecture set once by admin, not re-entered by the
teacher each time.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd1a7e4c95f2b'
down_revision = 'c9f6a2e83b5d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('lectures', sa.Column('room', sa.String(length=50), nullable=True))
    op.add_column('lectures', sa.Column('scheduled_start_time', sa.Time(), nullable=True))
    op.add_column('lectures', sa.Column('scheduled_end_time', sa.Time(), nullable=True))


def downgrade() -> None:
    op.drop_column('lectures', 'scheduled_end_time')
    op.drop_column('lectures', 'scheduled_start_time')
    op.drop_column('lectures', 'room')