"""auth: Head of Department / Coordinator role (HOD) and two permissions it shares with ADMIN

- attendance.correction.decide: decide teachers' attendance correction requests, see unmarked classes
- academic.oversight: see every teacher's course progress

Revision ID: f6a7b8c9d0e1
Revises: f5e6a7b8c9d0
"""
import sqlalchemy as sa
from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "f5e6a7b8c9d0"
branch_labels = None
depends_on = None

NEW = {"attendance.correction.decide": "Decide attendance correction requests", "academic.oversight": "See every teacher's course progress"}
HOD_GRANTS = ["attendance.correction.decide", "academic.oversight", "attendance.view", "course.view", "enrollment.view", "student.view",
              "application.submit", "task.use", "purchase.request"]


def _grant(role: str, code: str) -> None:
    op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
                       "WHERE r.code = :r AND p.code = :c AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)")
               .bindparams(r=role, c=code))


def upgrade() -> None:
    for code, desc in NEW.items():
        op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
                           "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=desc))
    op.execute(sa.text("INSERT INTO roles (id, code, name, created_at, updated_at) SELECT gen_random_uuid(), 'HOD', 'Head of Department / Coordinator', now(), now() "
                       "WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'HOD')"))
    for code in NEW:
        _grant("ADMIN", code)
    for code in HOD_GRANTS:
        _grant("HOD", code)


def downgrade() -> None:
    op.execute("DELETE FROM user_roles WHERE role_id IN (SELECT id FROM roles WHERE code = 'HOD')")
    op.execute("DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE code = 'HOD')")
    op.execute("DELETE FROM roles WHERE code = 'HOD'")
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('attendance.correction.decide', 'academic.oversight'))")
    op.execute("DELETE FROM permissions WHERE code IN ('attendance.correction.decide', 'academic.oversight')")