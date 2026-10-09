"""policy: administrator-editable institute policy values (attendance thresholds, promotion rules, fee grace days)

Revision ID: f3c4d5e6a7b8
Revises: f2b3c4d5e6a7
"""
import sqlalchemy as sa
from alembic import op

revision = "f3c4d5e6a7b8"
down_revision = "f2b3c4d5e6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policy_settings",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", sa.String(50), nullable=False),
        sa.Column("updated_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Give the administrator the new permission (scripts/seed_permissions.py also does this on a fresh install).
    op.execute("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), 'policy.manage', 'Change institute policy values', now(), now() WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'policy.manage')")
    op.execute("""INSERT INTO role_permissions (role_id, permission_id)
                  SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = 'ADMIN' AND p.code = 'policy.manage'
                  AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)""")


def downgrade() -> None:
    op.drop_table("policy_settings")
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'policy.manage')")
    op.execute("DELETE FROM permissions WHERE code = 'policy.manage'")