"""Datesheets for a whole session: exam_schedules.session_id.

Production impact: one NULLABLE column on exam_schedules (session_id, with an index and a foreign key to
academic_sessions, ON DELETE RESTRICT so a session with its own datesheets cannot be removed). Existing datesheets keep
NULL and work exactly as before (one term each). No data changes, no table rewrite (a small table). Downgrade drops
the column; do not downgrade after session datesheets were made (they would look like datesheets of a term with the
session's name).

Revision ID: e1a3c5e7b9d2
Revises: d9f1b3c5e7a2
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e1a3c5e7b9d2'
down_revision = 'd9f1b3c5e7a2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('exam_schedules', sa.Column('session_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_exam_schedules_session_id'), 'exam_schedules', ['session_id'], unique=False)
    op.create_foreign_key(op.f('fk_exam_schedules_session_id_academic_sessions'), 'exam_schedules', 'academic_sessions', ['session_id'], ['id'], ondelete='RESTRICT')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_exam_schedules_session_id_academic_sessions'), 'exam_schedules', type_='foreignkey')
    op.drop_index(op.f('ix_exam_schedules_session_id'), table_name='exam_schedules')
    op.drop_column('exam_schedules', 'session_id')
