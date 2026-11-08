"""1Bill: a record of every payment the bank network reports.

Revision ID: c4e6a8b0d2f4
Revises: b3d5f7a9c1e2
Create Date: 2026-11-08 16:56:14.012023

ADDITIVE and safe to run while the old version of the app is still serving: one new, empty table. No existing table or
row is touched. Downgrade drops it (the fee payments and receipts made through 1Bill stay, they are ordinary payments).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4e6a8b0d2f4'
down_revision = 'b3d5f7a9c1e2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('one_bill_transactions',
    sa.Column('consumer_number', sa.String(length=30), nullable=False),
    sa.Column('tran_auth_id', sa.String(length=30), nullable=False),
    sa.Column('bank_mnemonic', sa.String(length=20), server_default='', nullable=False),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('fee_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('late_fee_amount', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False),
    sa.Column('convenience_amount', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False),
    sa.Column('tran_date', sa.Date(), nullable=True),
    sa.Column('tran_time', sa.String(length=6), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('received_on', sa.Date(), nullable=False),
    sa.Column('challan_id', sa.Uuid(), nullable=False),
    sa.Column('receipt_number', sa.String(length=40), nullable=True),
    sa.Column('fine_receipt_numbers', sa.String(length=200), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('amount > 0', name=op.f('ck_one_bill_transactions_positive_amount')),
    sa.ForeignKeyConstraint(['challan_id'], ['payment_challans.id'], name=op.f('fk_one_bill_transactions_challan_id_payment_challans'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_one_bill_transactions')),
    sa.UniqueConstraint('bank_mnemonic', 'tran_auth_id', 'consumer_number', name='uq_one_bill_tx_bank_auth_consumer')
    )
    op.create_index('ix_one_bill_tx_received_on', 'one_bill_transactions', ['received_on'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_one_bill_tx_received_on', table_name='one_bill_transactions')
    op.drop_table('one_bill_transactions')
