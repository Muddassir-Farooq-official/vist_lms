"""staff: a general Staff role (office boy, IT, driver, cleaner...) with its own small portal

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7

Production impact: data only, no table changes. Adds one role (STAFF), one job title ("General Staff") and four grants of
permissions that already exist (any that a copy lacks is added). Nobody's access changes until the administration gives someone the new job title.
Safe to run while the site is up; it takes well under a second.
"""
import sqlalchemy as sa
from alembic import op

revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None

# (code, description) -- all four already exist on a seeded site; inserted here only if a copy lacks one.
GRANTS = (("application.submit", "Submit applications (leave, certificates...) to the administration"),
          ("task.use", "Assign tasks to other staff and respond to tasks assigned to me"),
          ("purchase.request", "Ask for something to be bought"),
          ("complaint.submit", "File and follow my own complaints"))


def upgrade() -> None:
    op.execute(sa.text("INSERT INTO roles (id, code, name, created_at, updated_at) SELECT gen_random_uuid(), 'STAFF', 'General Staff', now(), now() "
                       "WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'STAFF')"))
    op.execute(sa.text("INSERT INTO job_roles (id, code, title, default_role_code, is_active, created_at, updated_at) "
                       "SELECT gen_random_uuid(), 'STAFF', 'General Staff', 'STAFF', true, now(), now() "
                       "WHERE NOT EXISTS (SELECT 1 FROM job_roles WHERE code = 'STAFF')"))
    for code, description in GRANTS:
        op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
                           "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=description))
        op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = 'STAFF' AND p.code = :c "
                           "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(c=code))


def downgrade() -> None:
    # Only removes what is unused: a job title someone holds, or a role someone has, stays.
    op.execute("DELETE FROM job_roles WHERE code = 'STAFF' AND NOT EXISTS (SELECT 1 FROM employees e WHERE e.job_role_id = job_roles.id)")
    op.execute("DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE code = 'STAFF' "
               "AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.role_id = roles.id))")
    op.execute("DELETE FROM roles WHERE code = 'STAFF' AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.role_id = roles.id)")
