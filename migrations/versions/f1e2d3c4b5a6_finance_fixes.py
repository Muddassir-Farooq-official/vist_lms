"""Finance fixes: challan payments settle their own installment, reversals link both ways, salary advances need
approval, payroll is submitted before approval, and the accounts office no longer has staff attendance.

Revision ID: f1e2d3c4b5a6
Revises: e3f4a5b6c7d8
Create Date: 2026-10-24 00:00:00.000000

Everything here is ADDITIVE and safe to run while the old version of the app is still serving:
- new nullable columns only (no rewrite of existing rows, no table lock beyond a moment);
- new enum VALUES (the old code never writes them);
- two permission rows removed from one role (the accounts office loses staff_attendance.mark / .view).
No existing financial row is changed. Older challans and reversals keep working: the code recognises them.

Downgrade removes the columns and gives the permissions back. PostgreSQL cannot remove enum values; they stay,
unused, which is harmless. Do not downgrade after new advances or submitted payroll runs exist (their status
values would no longer be understood by the old code).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f1e2d3c4b5a6"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None

USERS = "users.id"


def upgrade() -> None:
    # Enum values cannot be added inside a transaction block on older PostgreSQL versions; do them on their own.
    with op.get_context().autocommit_block():
        for value in ("REQUESTED", "APPROVED", "REJECTED", "CANCELLED"):
            op.execute(f"ALTER TYPE advance_status ADD VALUE IF NOT EXISTS '{value}'")
        op.execute("ALTER TYPE payroll_status ADD VALUE IF NOT EXISTS 'SUBMITTED'")

    # Fees: a payment made against an installment's own challan, and the payment that paid a challan.
    op.add_column("fee_ledger_entries", sa.Column("applies_to_entry_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_fee_ledger_entries_applies_to_entry_id_fee_ledger_entries", "fee_ledger_entries", "fee_ledger_entries",
                          ["applies_to_entry_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_fee_ledger_entries_applies_to_entry_id", "fee_ledger_entries", ["applies_to_entry_id"])
    op.add_column("payment_challans", sa.Column("paid_entry_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_payment_challans_paid_entry_id_fee_ledger_entries", "payment_challans", "fee_ledger_entries",
                          ["paid_entry_id"], ["id"], ondelete="SET NULL")

    # Salary advances: who asked, who decided, who paid.
    for col in ("requested_by", "decided_by", "paid_by"):
        op.add_column("salary_advances", sa.Column(col, postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(f"fk_salary_advances_{col}_users", "salary_advances", "users", [col], ["id"], ondelete="SET NULL")
    op.add_column("salary_advances", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("salary_advances", sa.Column("decision_note", sa.String(length=300), nullable=True))
    op.add_column("salary_advances", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("salary_advances", sa.Column("payment_reference", sa.String(length=80), nullable=True))

    # Payroll runs: sent for approval by the accounts office (and, if sent back, why).
    op.add_column("payroll_runs", sa.Column("submitted_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_payroll_runs_submitted_by_users", "payroll_runs", "users", ["submitted_by"], ["id"], ondelete="SET NULL")
    op.add_column("payroll_runs", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("payroll_runs", sa.Column("returned_reason", sa.Text(), nullable=True))

    # The accounts office prepares payroll but no longer sees or marks staff attendance.
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE role_id IN (SELECT id FROM roles WHERE code = 'ACCOUNTS_OFFICER')
          AND permission_id IN (SELECT id FROM permissions WHERE code IN ('staff_attendance.mark', 'staff_attendance.view'))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE r.code = 'ACCOUNTS_OFFICER' AND p.code IN ('staff_attendance.mark', 'staff_attendance.view')
        ON CONFLICT DO NOTHING
        """
    )
    op.drop_column("payroll_runs", "returned_reason")
    op.drop_column("payroll_runs", "submitted_at")
    op.drop_constraint("fk_payroll_runs_submitted_by_users", "payroll_runs", type_="foreignkey")
    op.drop_column("payroll_runs", "submitted_by")
    for col in ("payment_reference", "paid_at", "decision_note", "decided_at"):
        op.drop_column("salary_advances", col)
    for col in ("paid_by", "decided_by", "requested_by"):
        op.drop_constraint(f"fk_salary_advances_{col}_users", "salary_advances", type_="foreignkey")
        op.drop_column("salary_advances", col)
    op.drop_constraint("fk_payment_challans_paid_entry_id_fee_ledger_entries", "payment_challans", type_="foreignkey")
    op.drop_column("payment_challans", "paid_entry_id")
    op.drop_index("ix_fee_ledger_entries_applies_to_entry_id", table_name="fee_ledger_entries")
    op.drop_constraint("fk_fee_ledger_entries_applies_to_entry_id_fee_ledger_entries", "fee_ledger_entries", type_="foreignkey")
    op.drop_column("fee_ledger_entries", "applies_to_entry_id")
