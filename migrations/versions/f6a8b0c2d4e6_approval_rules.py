"""approval rules: who approves each kind of action, by who asks and how much

Production impact: one new, empty table (approval_rules). With no rules every approval works exactly as before; an
administrator adds rules on Access control -> Approvals. Downgrade drops the table.

Revision ID: f6a8b0c2d4e6
Revises: e5f7a9b1c3d4
Create Date: 2026-11-27 22:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = 'f6a8b0c2d4e6'
down_revision = 'e5f7a9b1c3d4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'approval_rules',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('action', sa.String(40), nullable=False),
        sa.Column('requester_role', sa.String(32), nullable=True),
        sa.Column('min_amount', sa.Numeric(14, 2), nullable=True),
        sa.Column('mode', sa.String(10), nullable=False),
        sa.Column('approver_type', sa.String(16), nullable=True),
        sa.Column('approver_value', sa.String(64), nullable=True),
        sa.Column('note', sa.String(200), nullable=True),
        sa.Column('created_by', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL', name='fk_approval_rules_created_by_users'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_approval_rules'),
        sa.CheckConstraint("mode IN ('NONE', 'APPROVE')", name='ck_approval_rules_mode'),
    )
    op.create_index('ix_approval_rules_action', 'approval_rules', ['action'])


def downgrade() -> None:
    op.drop_index('ix_approval_rules_action', table_name='approval_rules')
    op.drop_table('approval_rules')
