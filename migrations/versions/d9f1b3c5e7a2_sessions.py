"""Academic sessions: "Fall 2026" groups the batch terms that run at the same time.

Production impact: one NEW table (academic_sessions, empty) and one NULLABLE column on academic_terms (session_id,
with an index and a foreign key; academic_terms is a small table, a few dozen rows). Every existing term starts in no
session, so nothing changes for anyone until an administrator groups terms into sessions. No record that uses a term
(offerings, fees, attendance, results, datesheets) is touched. Downgrade drops the column and the table (only the
grouping is lost).

Revision ID: d9f1b3c5e7a2
Revises: c7e9a1b3d5f8
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd9f1b3c5e7a2'
down_revision = 'c7e9a1b3d5f8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('academic_sessions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=50), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_academic_sessions_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_academic_sessions')),
    sa.UniqueConstraint('name', name='uq_academic_sessions_name')
    )
    op.add_column('academic_terms', sa.Column('session_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_academic_terms_session_id'), 'academic_terms', ['session_id'], unique=False)
    op.create_foreign_key(op.f('fk_academic_terms_session_id_academic_sessions'), 'academic_terms', 'academic_sessions', ['session_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_academic_terms_session_id_academic_sessions'), 'academic_terms', type_='foreignkey')
    op.drop_index(op.f('ix_academic_terms_session_id'), table_name='academic_terms')
    op.drop_column('academic_terms', 'session_id')
    op.drop_table('academic_sessions')
