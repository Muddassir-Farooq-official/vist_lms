"""Finance controls: cancelled refunds, security deposits paid back, purchase price approval.

Revision ID: e4a0b1c2d345
Revises: d39fa0b1c234
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e4a0b1c2d345"
down_revision = "d39fa0b1c234"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE fee_refund_status ADD VALUE IF NOT EXISTS 'CANCELLED'")
    op.add_column("employee_payroll_settings", sa.Column("security_released", sa.Numeric(12, 2), nullable=False, server_default="0"))
    op.add_column("employee_payroll_settings", sa.Column("security_released_on", sa.Date(), nullable=True))
    op.add_column("purchase_orders", sa.Column("price_approved_by", sa.Uuid(), nullable=True))
    op.add_column("purchase_orders", sa.Column("price_approved_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_purchase_orders_price_approved_by_users", "purchase_orders", "users", ["price_approved_by"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_purchase_orders_price_approved_by_users", "purchase_orders", type_="foreignkey")
    op.drop_column("purchase_orders", "price_approved_at")
    op.drop_column("purchase_orders", "price_approved_by")
    op.drop_column("employee_payroll_settings", "security_released_on")
    op.drop_column("employee_payroll_settings", "security_released")
    # PostgreSQL cannot drop an enum value; CANCELLED stays in fee_refund_status (unused).