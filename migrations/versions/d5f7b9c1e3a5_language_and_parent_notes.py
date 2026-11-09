"""Language choice per account; what the last Monday note to parents said.

Revision ID: d5f7b9c1e3a5
Revises: c4e6a8b0d2f4
Create Date: 2026-11-09 10:34:32.347694

ADDITIVE and safe to run while the old version of the app is still serving:
* users.language: a new column with a default ("en"), so every existing account keeps English. On PostgreSQL adding a
  column with a constant default does not rewrite the table (fast, no long lock).
* parent_alert_states: a new, empty table.
Downgrade drops both (the language choices and the note history).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5f7b9c1e3a5'
down_revision = 'c4e6a8b0d2f4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('parent_alert_states',
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('signature', sa.String(length=500), server_default='', nullable=False),
    sa.Column('sent_on', sa.Date(), nullable=True),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_parent_alert_states_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('student_id', name=op.f('pk_parent_alert_states'))
    )
    op.add_column('users', sa.Column('language', sa.String(length=5), server_default='en', nullable=False))


def downgrade() -> None:
    op.drop_column('users', 'language')
    op.drop_table('parent_alert_states')
