"""payroll: pay items, salary advances, monthly payroll runs and payslips

Revision ID: d9f5b3e7a128
Revises: c8e4a2d6f917
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d9f5b3e7a128"
down_revision = "c8e4a2d6f917"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uid = lambda: postgresql.UUID(as_uuid=True)                                                          # noqa: E731
    user = lambda name: sa.Column(name, uid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)      # noqa: E731
    stamps = lambda: (sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))   # noqa: E731
    method = lambda nullable: postgresql.ENUM("CASH", "BANK_TRANSFER", "CHEQUE", "ONLINE", name="account_payment_method", create_type=False)     # noqa: E731
    op.create_table(
        "employee_pay_items",
        sa.Column("id", uid(), primary_key=True),
        sa.Column("employee_id", uid(), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", postgresql.ENUM("ALLOWANCE", "DEDUCTION", name="pay_item_kind"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("calc", postgresql.ENUM("FIXED", "PERCENT_OF_BASE", name="pay_item_calc"), nullable=False),
        sa.Column("value", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("valid_from", sa.Date(), nullable=True), sa.Column("valid_until", sa.Date(), nullable=True), *stamps(),
    )
    op.create_index("ix_employee_pay_items_employee", "employee_pay_items", ["employee_id"])
    op.create_table(
        "salary_advances",
        sa.Column("id", uid(), primary_key=True),
        sa.Column("employee_id", uid(), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False), sa.Column("monthly_recovery", sa.Numeric(12, 2), nullable=False), sa.Column("balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", postgresql.ENUM("ACTIVE", "CLEARED", name="advance_status"), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False), user("granted_by"), sa.Column("granted_on", sa.Date(), nullable=False),
        sa.Column("payment_method", method(False), nullable=False),
        sa.Column("expense_id", uid(), sa.ForeignKey("expenses.id", ondelete="SET NULL"), nullable=True), *stamps(),
    )
    op.create_index("ix_salary_advances_employee", "salary_advances", ["employee_id"])
    op.create_table(
        "payroll_runs",
        sa.Column("id", uid(), primary_key=True),
        sa.Column("serial_no", sa.Integer(), sa.Identity(), nullable=False, unique=True),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("status", postgresql.ENUM("DRAFT", "APPROVED", "PAID", "CANCELLED", name="payroll_status"), nullable=False),
        sa.Column("employees_count", sa.Integer(), nullable=False), sa.Column("total_gross", sa.Numeric(14, 2), nullable=False), sa.Column("total_deductions", sa.Numeric(14, 2), nullable=False), sa.Column("total_net", sa.Numeric(14, 2), nullable=False),
        user("created_by"), user("approved_by"), sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        user("paid_by"), sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True), sa.Column("paid_date", sa.Date(), nullable=True),
        sa.Column("payment_method", method(True), nullable=True), sa.Column("payment_reference", sa.String(80), nullable=True),
        sa.Column("expense_id", uid(), sa.ForeignKey("expenses.id", ondelete="SET NULL"), nullable=True),
        user("cancelled_by"), sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True), sa.Column("cancel_reason", sa.Text(), nullable=True), *stamps(),
    )
    op.create_index("uq_payroll_runs_month_active", "payroll_runs", ["month"], unique=True, postgresql_where=sa.text("status <> 'CANCELLED'"))
    op.create_table(
        "payslips",
        sa.Column("id", uid(), primary_key=True),
        sa.Column("run_id", uid(), sa.ForeignKey("payroll_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", uid(), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_name", sa.String(200), nullable=False), sa.Column("employee_code", sa.String(40), nullable=False), sa.Column("job_title", sa.String(120), nullable=True), sa.Column("department", sa.String(120), nullable=True),
        sa.Column("base_salary", sa.Numeric(12, 2), nullable=False), sa.Column("days_in_month", sa.Integer(), nullable=False), sa.Column("days_payable", sa.Integer(), nullable=False),
        sa.Column("unpaid_days", sa.Numeric(4, 1), nullable=False, server_default="0"), sa.Column("adjustment", sa.Numeric(12, 2), nullable=False, server_default="0"), sa.Column("adjustment_note", sa.String(200), nullable=True),
        sa.Column("held", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("held_reason", sa.String(200), nullable=True),
        sa.Column("gross", sa.Numeric(12, 2), nullable=False), sa.Column("deductions", sa.Numeric(12, 2), nullable=False), sa.Column("net", sa.Numeric(12, 2), nullable=False), *stamps(),
        sa.UniqueConstraint("run_id", "employee_id", name="uq_payslips_run_employee"),
    )
    op.create_index("ix_payslips_employee", "payslips", ["employee_id"])
    op.create_table(
        "payslip_lines",
        sa.Column("id", uid(), primary_key=True),
        sa.Column("payslip_id", uid(), sa.ForeignKey("payslips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", postgresql.ENUM("EARNING", "DEDUCTION", name="payslip_line_kind"), nullable=False),
        sa.Column("source", sa.String(20), nullable=False), sa.Column("label", sa.String(160), nullable=False), sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("ref_id", uid(), nullable=True), *stamps(),
    )
    op.create_index("ix_payslip_lines_payslip", "payslip_lines", ["payslip_id"])
    for code, description, roles in (("payroll.view", "See payroll runs, payslips, pay items and advances", ["ADMIN", "ACCOUNTS_OFFICER"]),
                                     ("payroll.manage", "Prepare and pay payroll, set pay items, grant advances", ["ADMIN", "ACCOUNTS_OFFICER"]),
                                     ("payroll.approve", "Approve a payroll run before it is paid, and reverse payments", ["ADMIN"])):
        op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=description))
        for role in roles:
            op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = :r AND p.code = :c "
                               "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(r=role, c=code))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('payroll.view', 'payroll.manage', 'payroll.approve'))")
    op.execute("DELETE FROM permissions WHERE code IN ('payroll.view', 'payroll.manage', 'payroll.approve')")
    op.drop_index("ix_payslip_lines_payslip", table_name="payslip_lines"); op.drop_table("payslip_lines")
    op.drop_index("ix_payslips_employee", table_name="payslips"); op.drop_table("payslips")
    op.drop_index("uq_payroll_runs_month_active", table_name="payroll_runs"); op.drop_table("payroll_runs")
    op.drop_index("ix_salary_advances_employee", table_name="salary_advances"); op.drop_table("salary_advances")
    op.drop_index("ix_employee_pay_items_employee", table_name="employee_pay_items"); op.drop_table("employee_pay_items")
    for name in ("payslip_line_kind", "payroll_status", "advance_status", "pay_item_calc", "pay_item_kind"):
        op.execute(f"DROP TYPE IF EXISTS {name}")