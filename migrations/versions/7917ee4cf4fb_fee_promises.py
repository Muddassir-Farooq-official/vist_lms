"""Promises to pay overdue fees (fee defaulter follow-up).

Production impact: one new empty table fee_promises (with an index on student_id). No existing table or row changes.
Admit-card fee exemptions use the admit_card_exemptions table added in b6d8f0a2c4e7.

Revision ID: 7917ee4cf4fb
Revises: b6d8f0a2c4e7
Create Date: 2026-11-21 21:01:39.476378

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7917ee4cf4fb'
down_revision = 'b6d8f0a2c4e7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('fee_promises',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('promised_date', sa.Date(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('cancelled_by', sa.Uuid(), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['cancelled_by'], ['users.id'], name=op.f('fk_fee_promises_cancelled_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_fee_promises_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_fee_promises_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fee_promises'))
    )
    op.create_index(op.f('ix_fee_promises_student_id'), 'fee_promises', ['student_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_fee_promises_student_id'), table_name='fee_promises')
    op.drop_table('fee_promises')
