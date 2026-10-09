"""parents: Parent/Guardian role, parent-student links, and the permissions for the parent portal

Revision ID: f7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""
import sqlalchemy as sa
from alembic import op

revision = "f7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def _perm(code: str, desc: str) -> None:
    op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
                       "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=desc))


def _grant(role: str, code: str) -> None:
    op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
                       "WHERE r.code = :r AND p.code = :c AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)")
               .bindparams(r=role, c=code))


def upgrade() -> None:
    op.create_table(
        "parent_student_links",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("student_id", sa.Uuid(), sa.ForeignKey("students.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    _perm("parent.view", "Parent portal: see own linked children")
    _perm("parent.access.manage", "Create / link / unlink a guardian's parent login")
    op.execute(sa.text("INSERT INTO roles (id, code, name, created_at, updated_at) SELECT gen_random_uuid(), 'PARENT', 'Parent / Guardian', now(), now() "
                       "WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'PARENT')"))
    _grant("PARENT", "parent.view")
    _grant("ADMIN", "parent.access.manage")


def downgrade() -> None:
    op.drop_table("parent_student_links")
    op.execute("DELETE FROM user_roles WHERE role_id IN (SELECT id FROM roles WHERE code = 'PARENT')")
    op.execute("DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE code = 'PARENT')")
    op.execute("DELETE FROM roles WHERE code = 'PARENT'")
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('parent.view', 'parent.access.manage'))")
    op.execute("DELETE FROM permissions WHERE code IN ('parent.view', 'parent.access.manage')")