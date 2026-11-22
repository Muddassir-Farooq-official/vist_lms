"""Substitute teachers for classes missed during approved leave.

Production impact: one new empty table class_substitutes. No existing table or row changes.

Revision ID: 1513e95a01c9
Revises: 8f4986730b29
Create Date: 2026-11-22 11:57:58.404186

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1513e95a01c9'
down_revision = '8f4986730b29'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('class_substitutes',
    sa.Column('class_session_id', sa.Uuid(), nullable=False),
    sa.Column('leave_request_id', sa.Uuid(), nullable=True),
    sa.Column('absent_employee_id', sa.Uuid(), nullable=False),
    sa.Column('substitute_employee_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('proposed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['absent_employee_id'], ['employees.id'], name=op.f('fk_class_substitutes_absent_employee_id_employees'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['class_session_id'], ['class_sessions.id'], name=op.f('fk_class_substitutes_class_session_id_class_sessions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_class_substitutes_decided_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['leave_request_id'], ['leave_requests.id'], name=op.f('fk_class_substitutes_leave_request_id_leave_requests'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['substitute_employee_id'], ['employees.id'], name=op.f('fk_class_substitutes_substitute_employee_id_employees'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('class_session_id', name=op.f('pk_class_substitutes'))
    )
    op.create_index(op.f('ix_class_substitutes_leave_request_id'), 'class_substitutes', ['leave_request_id'], unique=False)
    op.create_index('ix_class_substitutes_status', 'class_substitutes', ['status'], unique=False)
    op.create_index('ix_class_substitutes_substitute', 'class_substitutes', ['substitute_employee_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_class_substitutes_substitute', table_name='class_substitutes')
    op.drop_index('ix_class_substitutes_status', table_name='class_substitutes')
    op.drop_index(op.f('ix_class_substitutes_leave_request_id'), table_name='class_substitutes')
    op.drop_table('class_substitutes')
