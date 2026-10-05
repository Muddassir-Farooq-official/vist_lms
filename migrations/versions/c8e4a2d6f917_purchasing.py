"""purchasing: purchase requests that are approved, ordered, received and paid (and then become expenses)

Revision ID: c8e4a2d6f917
Revises: b7d3f9a1c845
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c8e4a2d6f917"
down_revision = "b7d3f9a1c845"
branch_labels = None
depends_on = None

STAFF = ["ADMIN", "TEACHER", "LIBRARIAN", "ACCOUNTS_OFFICER", "HOSTEL_WARDEN", "SECURITY_GUARD"]


def upgrade() -> None:
    uid = lambda name, **kw: sa.Column(name, postgresql.UUID(as_uuid=True), **kw)                      # noqa: E731
    user_fk = lambda name: uid(name, nullable=True) if False else sa.Column(name, postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)   # noqa: E731
    op.create_table(
        "purchase_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("serial_no", sa.Integer(), sa.Identity(), nullable=False, unique=True),
        user_fk("requested_by"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("needed_by", sa.Date(), nullable=True),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("account_categories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("suggested_vendor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", postgresql.ENUM("REQUESTED", "APPROVED", "REJECTED", "ORDERED", "RECEIVED", "PAID", "CANCELLED", name="purchase_status"), nullable=False),
        sa.Column("estimated_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("actual_total", sa.Numeric(14, 2), nullable=True),
        user_fk("decided_by"), sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True), sa.Column("decision_note", sa.Text(), nullable=True),
        user_fk("ordered_by"), sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=True), sa.Column("expected_delivery", sa.Date(), nullable=True), sa.Column("order_note", sa.Text(), nullable=True),
        user_fk("received_by"), sa.Column("received_at", sa.DateTime(timezone=True), nullable=True), sa.Column("invoice_no", sa.String(80), nullable=True),
        user_fk("paid_by"), sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_method", postgresql.ENUM("CASH", "BANK_TRANSFER", "CHEQUE", "ONLINE", name="account_payment_method", create_type=False), nullable=True),
        sa.Column("payment_reference", sa.String(80), nullable=True),
        sa.Column("expense_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("expenses.id", ondelete="SET NULL"), nullable=True),
        user_fk("cancelled_by"), sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True), sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_purchase_orders_status", "purchase_orders", ["status"])
    op.create_index("ix_purchase_orders_requested_by", "purchase_orders", ["requested_by"])
    op.create_table(
        "purchase_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 2), nullable=False),
        sa.Column("estimated_unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("received_quantity", sa.Numeric(10, 2), nullable=True),
        sa.Column("actual_unit_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_purchase_items_order", "purchase_items", ["order_id"])
    op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), 'purchase.request', 'Ask for something to be bought, and follow the request', now(), now() "
                       "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'purchase.request')"))
    for role in STAFF:
        op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = :r AND p.code = 'purchase.request' "
                           "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(r=role))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'purchase.request')")
    op.execute("DELETE FROM permissions WHERE code = 'purchase.request'")
    op.drop_index("ix_purchase_items_order", table_name="purchase_items")
    op.drop_table("purchase_items")
    op.drop_index("ix_purchase_orders_requested_by", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_status", table_name="purchase_orders")
    op.drop_table("purchase_orders")
    op.execute("DROP TYPE IF EXISTS purchase_status")