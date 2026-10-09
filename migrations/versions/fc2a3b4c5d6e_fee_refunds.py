"""fees: refunds (fee_refunds), the REFUND ledger entry type, and the fee.refund.approve permission (ADMIN)

Revision ID: fc2a3b4c5d6e
Revises: fb1f2a3b4c5d
"""
import sqlalchemy as sa
from alembic import op

revision = "fc2a3b4c5d6e"
down_revision = "fb1f2a3b4c5d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():           # a new enum value must be committed before it is used
        op.execute("ALTER TYPE fee_ledger_entry_type ADD VALUE IF NOT EXISTS 'REFUND'")
    kind = sa.Enum("OVERPAYMENT", "WITHDRAWAL", "OTHER", name="fee_refund_kind")
    status = sa.Enum("REQUESTED", "APPROVED", "REJECTED", "PAID", name="fee_refund_status")
    op.create_table(
        "fee_refunds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("fee_assignment_id", sa.Uuid(), sa.ForeignKey("fee_assignments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", kind, nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("write_off", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(500), nullable=True),
        sa.Column("paid_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ledger_entry_id", sa.Uuid(), sa.ForeignKey("fee_ledger_entries.id", ondelete="SET NULL"), nullable=True),
        sa.Column("voucher_number", sa.String(30), nullable=True),
        sa.Column("paid_on", sa.Date(), nullable=True),
    )
    op.create_index("ix_fee_refunds_fee_assignment_id", "fee_refunds", ["fee_assignment_id"])
    op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), 'fee.refund.approve', "
                       "'Approve or reject fee refunds', now(), now() WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'fee.refund.approve')"))
    op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
                       "WHERE r.code = 'ADMIN' AND p.code = 'fee.refund.approve' AND NOT EXISTS "
                       "(SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)"))


def downgrade() -> None:
    op.drop_table("fee_refunds")
    sa.Enum(name="fee_refund_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="fee_refund_kind").drop(op.get_bind(), checkfirst=True)
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'fee.refund.approve')")
    op.execute("DELETE FROM permissions WHERE code = 'fee.refund.approve'")
    # PostgreSQL cannot drop an enum value; REFUND stays on fee_ledger_entry_type (unused after downgrade).