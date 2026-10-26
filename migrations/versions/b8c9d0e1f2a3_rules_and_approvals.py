"""Owner decisions: late-fee pause, advance credit moves, second-person approvals.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-25 00:00:00.000000

Everything here is ADDITIVE and safe to run while the old version of the app is still serving:
- two new enum VALUES on fee_ledger_entry_type (CREDIT_OUT, CREDIT_IN) for moving advance credit between fees;
  the old code never writes them;
- two new nullable columns on fee_assignments (late_fee_paused_from / _until);
- one new table, approval_requests;
- two new permissions: scholarship.propose (ADMIN, ACCOUNTS_OFFICER) and approvals.decide (ADMIN).
No existing row is changed.

Downgrade drops the table, the columns and the permissions. PostgreSQL cannot remove enum values; they stay, unused.
Do not downgrade after credit has been moved between fees (the old code would not understand those rows).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None

GRANTS = [
    ("scholarship.propose", "Propose a scholarship or discount (an administrator approves it)", ["ADMIN", "ACCOUNTS_OFFICER"]),
    ("approvals.decide", "Approve or reject requests that need a second person (waivers, write-offs, reopening books, scholarships)", ["ADMIN"]),
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in ("CREDIT_OUT", "CREDIT_IN"):
            op.execute(f"ALTER TYPE fee_ledger_entry_type ADD VALUE IF NOT EXISTS '{value}'")

    op.add_column("fee_assignments", sa.Column("late_fee_paused_from", sa.Date(), nullable=True))
    op.add_column("fee_assignments", sa.Column("late_fee_paused_until", sa.Date(), nullable=True))

    op.create_table(
        "approval_requests",
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("summary", sa.String(length=300), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(length=300), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], name=op.f("fk_approval_requests_decided_by_users"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], name=op.f("fk_approval_requests_requested_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests")),
    )
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"], unique=False)
    op.create_index("ix_approval_requests_target", "approval_requests", ["kind", "target_id"], unique=False)

    for code, description, roles in GRANTS:
        op.execute(sa.text(
            "INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
            "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=description))
        for role in roles:
            op.execute(sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = :r AND p.code = :c "
                "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(r=role, c=code))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('scholarship.propose', 'approvals.decide'))")
    op.execute("DELETE FROM permissions WHERE code IN ('scholarship.propose', 'approvals.decide')")
    op.drop_index("ix_approval_requests_target", table_name="approval_requests")
    op.drop_index("ix_approval_requests_status", table_name="approval_requests")
    op.drop_table("approval_requests")
    op.drop_column("fee_assignments", "late_fee_paused_until")
    op.drop_column("fee_assignments", "late_fee_paused_from")
