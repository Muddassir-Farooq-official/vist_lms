"""Admin "view as" and the activity log: permissions only.

Adds two permissions for the administration:
- view_as.use: open a teacher's, student's or parent's own screens read-only;
- audit.export: download the activity log as CSV;
and gives the principal audit.view (read the activity log; no export).

Production impact: three small INSERTs into permissions / role_permissions. No table changes, no locks on
busy tables, no existing row is changed. Safe to run at any time. Downgrade removes exactly these grants.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
"""
from alembic import op
import sqlalchemy as sa

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None

NEW = [
    ("view_as.use", "Open a teacher's, student's or parent's screens read-only (administration)", ["ADMIN"]),
    ("audit.export", "Download the activity log as CSV (administration)", ["ADMIN"]),
]
EXTRA_GRANTS = [("audit.view", "PRINCIPAL")]


def _grant(code: str, role: str) -> None:
    op.execute(sa.text(
        "INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = :r AND p.code = :c "
        "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(r=role, c=code))


def upgrade() -> None:
    for code, description, roles in NEW:
        op.execute(sa.text(
            "INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
            "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=description))
        for role in roles:
            _grant(code, role)
    for code, role in EXTRA_GRANTS:
        _grant(code, role)


def downgrade() -> None:
    op.execute(sa.text(
        "DELETE FROM role_permissions WHERE role_id = (SELECT id FROM roles WHERE code = 'PRINCIPAL') "
        "AND permission_id = (SELECT id FROM permissions WHERE code = 'audit.view')"))
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('view_as.use', 'audit.export'))")
    op.execute("DELETE FROM permissions WHERE code IN ('view_as.use', 'audit.export')")
