"""Semester rollover wizard: each promotion, kept for undo (7 days) and for the record.

Production impact: one new empty table rollover_runs. No existing table or row changes.

Revision ID: bdfd05173b5a
Revises: df4574befe5a
Create Date: 2026-11-22 09:58:20.614696

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'bdfd05173b5a'
down_revision = 'df4574befe5a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('rollover_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=True),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('undone_by', sa.Uuid(), nullable=True),
    sa.Column('undone_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_rollover_runs_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['session_id'], ['academic_sessions.id'], name=op.f('fk_rollover_runs_session_id_academic_sessions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['undone_by'], ['users.id'], name=op.f('fk_rollover_runs_undone_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rollover_runs'))
    )
    op.create_index(op.f('ix_rollover_runs_session_id'), 'rollover_runs', ['session_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_rollover_runs_session_id'), table_name='rollover_runs')
    op.drop_table('rollover_runs')
