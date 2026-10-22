"""inventory: stock items, the append-only stock movement ledger, and the inventory permissions

Revision ID: ff5b6c7d8e9f
Revises: fe4a5b6c7d8e
"""
import sqlalchemy as sa
from alembic import op

revision = "ff5b6c7d8e9f"
down_revision = "fe4a5b6c7d8e"
branch_labels = None
depends_on = None

PERMS = (
    ("inventory.view", "See the store: stock, history, who has what", ["ADMIN", "ACCOUNTS_OFFICER", "PRINCIPAL"]),
    ("inventory.manage", "Add items, receive purchases into stock, issue and take back items", ["ADMIN", "ACCOUNTS_OFFICER"]),
    ("inventory.adjust", "Correct stock after a count or loss, and reverse mistakes", ["ADMIN"]),
)


def upgrade() -> None:
    op.create_table(
        "stock_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("sale_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("reorder_level", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("is_returnable", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("quantity_on_hand", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("average_cost", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_stock_items_created_by_users"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_stock_items"),
        sa.UniqueConstraint("code", name="uq_stock_items_code"),
    )
    op.create_index("ix_stock_items_category", "stock_items", ["category"])

    kind = sa.Enum("OPENING", "RECEIVED", "ISSUED", "RETURNED", "ADJUSTED", "REVERSED", name="stock_movement_kind")
    mode = sa.Enum("FREE", "PAID_NOW", "ADD_TO_DUES", name="stock_charge_mode")
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("serial_no", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("item_id", sa.Uuid(), sa.ForeignKey("stock_items.id", ondelete="RESTRICT", name="fk_stock_movements_item_id_stock_items"), nullable=False),
        sa.Column("kind", kind, nullable=False),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("balance_after", sa.Numeric(12, 2), nullable=False),
        sa.Column("party_type", sa.String(20), nullable=True),
        sa.Column("party_id", sa.Uuid(), nullable=True),
        sa.Column("party_name", sa.String(200), nullable=True),
        sa.Column("charge_mode", mode, nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("charge_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("payment_method", sa.String(20), nullable=True),
        sa.Column("payment_reference", sa.String(80), nullable=True),
        sa.Column("receipt_number", sa.String(30), nullable=True),
        sa.Column("income_entry_id", sa.Uuid(), sa.ForeignKey("income_entries.id", ondelete="SET NULL", name="fk_stock_movements_income_entry_id_income_entries"), nullable=True),
        sa.Column("fine_id", sa.Uuid(), sa.ForeignKey("fines.id", ondelete="SET NULL", name="fk_stock_movements_fine_id_fines"), nullable=True),
        sa.Column("purchase_order_id", sa.Uuid(), sa.ForeignKey("purchase_orders.id", ondelete="SET NULL", name="fk_stock_movements_purchase_order_id_purchase_orders"), nullable=True),
        sa.Column("purchase_item_id", sa.Uuid(), sa.ForeignKey("purchase_items.id", ondelete="SET NULL", name="fk_stock_movements_purchase_item_id_purchase_items"), nullable=True),
        sa.Column("issue_id", sa.Uuid(), sa.ForeignKey("stock_movements.id", ondelete="SET NULL", name="fk_stock_movements_issue_id_stock_movements"), nullable=True),
        sa.Column("reverses_id", sa.Uuid(), sa.ForeignKey("stock_movements.id", ondelete="SET NULL", name="fk_stock_movements_reverses_id_stock_movements"), nullable=True),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("recorded_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_stock_movements_recorded_by_users"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_stock_movements"),
        sa.UniqueConstraint("serial_no", name="uq_stock_movements_serial_no"),
        sa.UniqueConstraint("receipt_number", name="uq_stock_movements_receipt_number"),
    )
    op.create_index("ix_stock_movements_item", "stock_movements", ["item_id"])
    op.create_index("ix_stock_movements_party", "stock_movements", ["party_type", "party_id"])
    op.create_index("ix_stock_movements_kind", "stock_movements", ["kind"])

    for code, description, roles in PERMS:
        op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
                           "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=description))
        for role in roles:
            op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
                               "WHERE r.code = :r AND p.code = :c AND NOT EXISTS "
                               "(SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(r=role, c=code))


def downgrade() -> None:
    op.drop_table("stock_movements")
    op.drop_table("stock_items")
    sa.Enum(name="stock_charge_mode").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="stock_movement_kind").drop(op.get_bind(), checkfirst=True)
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code LIKE 'inventory.%')")
    op.execute("DELETE FROM permissions WHERE code LIKE 'inventory.%'")