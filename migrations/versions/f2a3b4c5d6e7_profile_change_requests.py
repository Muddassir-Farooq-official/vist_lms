"""Self-service profile changes: students and employees ask, an administrator approves.

Adds:
- one new table, profile_change_requests (empty at first);
- one new permission, profile.change.decide, for Administrator.

Production impact: creates an empty table and two indexes on it, inserts one permission and one grant. No existing table or
row is changed; nothing is locked. Safe to run at any time.
Behaviour change that comes with the code (not the migration): a student's or employee's own photo upload now waits for an
administrator instead of being published at once. The administration's own uploads still publish at once.

Downgrade drops the table (any waiting requests are lost; approved changes stay on the people's records) and the permission.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_change_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_type", sa.String(length=10), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("photo_storage_key", sa.String(length=255), nullable=True),
        sa.Column("photo_original_name", sa.String(length=255), nullable=True),
        sa.Column("photo_check", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], name=op.f("fk_profile_change_requests_requested_by_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], name=op.f("fk_profile_change_requests_decided_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_change_requests")),
    )
    op.create_index("ix_profile_change_requests_status", "profile_change_requests", ["status"], unique=False)
    op.create_index("ix_profile_change_requests_subject", "profile_change_requests", ["subject_type", "subject_id"], unique=False)
    op.execute(sa.text(
        "INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
        "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(
        c="profile.change.decide", d="Approve or reject students' and staff's own profile changes and photos (administration)"))
    op.execute(sa.text(
        "INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = 'ADMIN' "
        "AND p.code = 'profile.change.decide' AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)"))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'profile.change.decide')")
    op.execute("DELETE FROM permissions WHERE code = 'profile.change.decide'")
    op.drop_index("ix_profile_change_requests_subject", table_name="profile_change_requests")
    op.drop_index("ix_profile_change_requests_status", table_name="profile_change_requests")
    op.drop_table("profile_change_requests")
