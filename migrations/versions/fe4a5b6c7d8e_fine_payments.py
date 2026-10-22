"""fines: how a fine was paid (method, reference, date, receipt number) and the INVENTORY fine source

Revision ID: fe4a5b6c7d8e
Revises: fd3b4c5d6e7f
"""
import sqlalchemy as sa
from alembic import op

revision = "fe4a5b6c7d8e"
down_revision = "fd3b4c5d6e7f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():           # a new enum value must be committed before it is used
        op.execute("ALTER TYPE fine_source ADD VALUE IF NOT EXISTS 'INVENTORY'")
    op.add_column("fines", sa.Column("payment_method", sa.String(20), nullable=True))
    op.add_column("fines", sa.Column("payment_reference", sa.String(80), nullable=True))
    op.add_column("fines", sa.Column("paid_on", sa.Date(), nullable=True))
    op.add_column("fines", sa.Column("receipt_number", sa.String(30), nullable=True))
    op.create_unique_constraint("uq_fines_receipt_number", "fines", ["receipt_number"])
    # Fines paid before this change: treat them as cash, paid the day they were marked paid.
    op.execute("UPDATE fines SET payment_method = 'CASH', paid_on = (resolved_at AT TIME ZONE 'Asia/Karachi')::date "
               "WHERE status = 'PAID' AND paid_on IS NULL AND resolved_at IS NOT NULL")


def downgrade() -> None:
    op.drop_constraint("uq_fines_receipt_number", "fines", type_="unique")
    for col in ("receipt_number", "paid_on", "payment_reference", "payment_method"):
        op.drop_column("fines", col)
    # PostgreSQL cannot drop an enum value; INVENTORY stays on fine_source (unused after downgrade).