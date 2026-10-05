"""accounts: expense and income categories, vendors, expenses (with receipts and an approval limit), other income

Revision ID: b7d3f9a1c845
Revises: e4b9a7c3d261
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b7d3f9a1c845"
down_revision = "e4b9a7c3d261"
branch_labels = None
depends_on = None

EXPENSE_CATEGORIES = [("Salaries and wages", True), ("Utilities (electricity, gas, water)", False), ("Rent", False), ("Repairs and maintenance", False), ("Stationery and supplies", False),
                      ("Equipment and furniture", False), ("Books and library", False), ("IT and internet", False), ("Transport and fuel", False), ("Events and activities", False),
                      ("Marketing and advertising", False), ("Taxes and government fees", False), ("Purchases", True), ("Miscellaneous", False)]
INCOME_CATEGORIES = [("Donations and grants", False), ("Canteen and cafeteria", False), ("Rent received", False), ("Hostel", False), ("Exam and other fees", False), ("Other income", False)]


def upgrade() -> None:
    op.execute("ALTER TYPE notification_category ADD VALUE IF NOT EXISTS 'FINANCE'")
    kind = postgresql.ENUM("INCOME", "EXPENSE", name="account_entry_kind")
    method = postgresql.ENUM("CASH", "BANK_TRANSFER", "CHEQUE", "ONLINE", name="account_payment_method")
    method.create(op.get_bind(), checkfirst=True)        # used by two tables below, which must not each try to create it
    op.create_table(
        "account_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", kind, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "name", name="uq_account_categories_kind_name"),
    )
    for k, rows in (("EXPENSE", EXPENSE_CATEGORIES), ("INCOME", INCOME_CATEGORIES)):
        for name, system in rows:
            op.execute(sa.text("INSERT INTO account_categories (id, kind, name, is_system, is_active, created_at, updated_at) VALUES (gen_random_uuid(), CAST(:k AS account_entry_kind), :n, :s, true, now(), now())").bindparams(k=k, n=name, s=system))
    op.create_table(
        "vendors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("contact_person", sa.String(120), nullable=True),
        sa.Column("phone", sa.String(30), nullable=True),
        sa.Column("email", sa.String(150), nullable=True),
        sa.Column("address", sa.String(300), nullable=True),
        sa.Column("tax_number", sa.String(40), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "expenses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("serial_no", sa.Integer(), sa.Identity(), nullable=False, unique=True),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("account_categories.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("payment_method", postgresql.ENUM("CASH", "BANK_TRANSFER", "CHEQUE", "ONLINE", name="account_payment_method", create_type=False), nullable=False),
        sa.Column("reference_no", sa.String(80), nullable=True),
        sa.Column("status", postgresql.ENUM("PENDING_APPROVAL", "APPROVED", "REJECTED", "VOIDED", name="expense_status"), nullable=False),
        sa.Column("source", postgresql.ENUM("MANUAL", "PURCHASE", "PAYROLL", name="expense_source"), nullable=False),
        sa.Column("source_ref_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recorded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, col in (("ix_expenses_date", "expense_date"), ("ix_expenses_status", "status"), ("ix_expenses_category", "category_id")):
        op.create_index(name, "expenses", [col])
    op.create_table(
        "expense_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("expense_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("expenses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_expense_attachments_expense", "expense_attachments", ["expense_id"])
    op.create_table(
        "income_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("serial_no", sa.Integer(), sa.Identity(), nullable=False, unique=True),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("account_categories.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("received_from", sa.String(150), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("income_date", sa.Date(), nullable=False),
        sa.Column("payment_method", postgresql.ENUM("CASH", "BANK_TRANSFER", "CHEQUE", "ONLINE", name="account_payment_method", create_type=False), nullable=False),
        sa.Column("reference_no", sa.String(80), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("recorded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_income_entries_date", "income_entries", ["income_date"])

    for code, description, roles in (("account.view", "See the accounts: expenses, income, the day book and reports", ["ADMIN", "ACCOUNTS_OFFICER"]),
                                     ("account.record", "Record expenses and other income, and manage vendors", ["ADMIN", "ACCOUNTS_OFFICER"]),
                                     ("account.approve", "Approve large expenses, void entries and manage categories", ["ADMIN"])):
        op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=description))
        for role in roles:
            op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = :r AND p.code = :c "
                               "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(r=role, c=code))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('account.view', 'account.record', 'account.approve'))")
    op.execute("DELETE FROM permissions WHERE code IN ('account.view', 'account.record', 'account.approve')")
    op.drop_index("ix_income_entries_date", table_name="income_entries")
    op.drop_table("income_entries")
    op.drop_index("ix_expense_attachments_expense", table_name="expense_attachments")
    op.drop_table("expense_attachments")
    for name in ("ix_expenses_category", "ix_expenses_status", "ix_expenses_date"):
        op.drop_index(name, table_name="expenses")
    op.drop_table("expenses")
    op.drop_table("vendors")
    op.drop_table("account_categories")
    for name in ("expense_source", "expense_status", "account_payment_method", "account_entry_kind"):
        op.execute(f"DROP TYPE IF EXISTS {name}")
    # (PostgreSQL cannot remove a value from the notification_category enum; FINANCE is left in place, unused.)