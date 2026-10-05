"""fees: scholarships and concessions (a new ledger entry type that reduces an installment)

Revision ID: e4b9a7c3d261
Revises: d3a8f6b2c159
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e4b9a7c3d261"
down_revision = "d3a8f6b2c159"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE fee_ledger_entry_type ADD VALUE IF NOT EXISTS 'CONCESSION'")
    op.create_table(
        "scholarships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", postgresql.ENUM("PERCENT", "FIXED", name="scholarship_kind"), nullable=False),
        sa.Column("value", sa.Numeric(10, 2), nullable=False),
        sa.Column("fee_structure_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fee_structures.id", ondelete="SET NULL"), nullable=True),
        sa.Column("auto_apply", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("status", postgresql.ENUM("ACTIVE", "ENDED", name="scholarship_status"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("granted_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("value > 0", name="positive_scholarship_value"),
    )
    op.create_index("ix_scholarships_student", "scholarships", ["student_id"])
    op.add_column("fee_ledger_entries", sa.Column("related_entry_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fee_ledger_entries.id", ondelete="CASCADE"), nullable=True))
    op.add_column("fee_ledger_entries", sa.Column("scholarship_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scholarships.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_fee_ledger_entries_related", "fee_ledger_entries", ["related_entry_id"])
    op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), 'scholarship.manage', 'Grant and end scholarships and concessions', now(), now() "
                       "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'scholarship.manage')"))
    op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = 'ADMIN' AND p.code = 'scholarship.manage' "
                       "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)"))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'scholarship.manage')")
    op.execute("DELETE FROM permissions WHERE code = 'scholarship.manage'")
    op.execute("DELETE FROM fee_ledger_entries WHERE entry_type = 'CONCESSION'")
    op.drop_index("ix_fee_ledger_entries_related", table_name="fee_ledger_entries")
    op.drop_column("fee_ledger_entries", "scholarship_id")
    op.drop_column("fee_ledger_entries", "related_entry_id")
    op.drop_index("ix_scholarships_student", table_name="scholarships")
    op.drop_table("scholarships")
    op.execute("DROP TYPE IF EXISTS scholarship_status")
    op.execute("DROP TYPE IF EXISTS scholarship_kind")
    # (PostgreSQL cannot remove a value from the fee_ledger_entry_type enum; CONCESSION is left in place, unused.)