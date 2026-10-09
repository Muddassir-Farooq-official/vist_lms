"""results: permission to import past (pre-system) results, granted to ADMIN

Revision ID: f4d5e6a7b8c9
Revises: f3c4d5e6a7b8
"""
from alembic import op

revision = "f4d5e6a7b8c9"
down_revision = "f3c4d5e6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), 'result.history.import', "
               "'Import past results', now(), now() WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'result.history.import')")
    op.execute("""INSERT INTO role_permissions (role_id, permission_id)
                  SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = 'ADMIN' AND p.code = 'result.history.import'
                  AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)""")


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'result.history.import')")
    op.execute("DELETE FROM permissions WHERE code = 'result.history.import'")