"""auth: Principal / Director role (read-only institute overview) and the institute.overview permission

Revision ID: f8c9d0e1f2a3
Revises: f7b8c9d0e1f2
"""
import sqlalchemy as sa
from alembic import op

revision = "f8c9d0e1f2a3"
down_revision = "f7b8c9d0e1f2"
branch_labels = None
depends_on = None

GRANTS = ["institute.overview", "academic.oversight", "attendance.view", "course.view", "enrollment.view", "student.view",
          "application.submit", "task.use", "purchase.request"]


def _grant(role: str, code: str) -> None:
    op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
                       "WHERE r.code = :r AND p.code = :c AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)")
               .bindparams(r=role, c=code))


def upgrade() -> None:
    op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), 'institute.overview', "
                       "'See the institute overview', now(), now() WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'institute.overview')"))
    op.execute(sa.text("INSERT INTO roles (id, code, name, created_at, updated_at) SELECT gen_random_uuid(), 'PRINCIPAL', 'Principal / Director', now(), now() "
                       "WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'PRINCIPAL')"))
    _grant("ADMIN", "institute.overview")
    for code in GRANTS:
        _grant("PRINCIPAL", code)


def downgrade() -> None:
    op.execute("DELETE FROM user_roles WHERE role_id IN (SELECT id FROM roles WHERE code = 'PRINCIPAL')")
    op.execute("DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE code = 'PRINCIPAL')")
    op.execute("DELETE FROM roles WHERE code = 'PRINCIPAL'")
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'institute.overview')")
    op.execute("DELETE FROM permissions WHERE code = 'institute.overview'")