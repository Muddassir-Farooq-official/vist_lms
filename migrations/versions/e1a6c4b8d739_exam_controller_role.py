"""exam department: an Exam Controller role that approves, publishes and corrects results and keeps the grading scale

Revision ID: e1a6c4b8d739
Revises: d9f5b3e7a128
"""
from alembic import op
import sqlalchemy as sa

revision = "e1a6c4b8d739"
down_revision = "d9f5b3e7a128"
branch_labels = None
depends_on = None

GRANTS = ["result.approve", "marks.enter", "student.view", "enrollment.view", "application.submit", "task.use", "purchase.request"]


def upgrade() -> None:
    op.execute(sa.text("INSERT INTO roles (id, code, name, created_at, updated_at) SELECT gen_random_uuid(), 'EXAM_CONTROLLER', 'Exam Controller', now(), now() "
                       "WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'EXAM_CONTROLLER')"))
    op.execute(sa.text("INSERT INTO job_roles (id, code, title, default_role_code, is_active, created_at, updated_at) SELECT gen_random_uuid(), 'EXAM_CONTROLLER', 'Exam Controller', 'EXAM_CONTROLLER', true, now(), now() "
                       "WHERE NOT EXISTS (SELECT 1 FROM job_roles WHERE code = 'EXAM_CONTROLLER')"))
    for code in GRANTS:
        op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = 'EXAM_CONTROLLER' AND p.code = :c "
                           "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(c=code))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE code = 'EXAM_CONTROLLER')")
    op.execute("DELETE FROM job_roles WHERE code = 'EXAM_CONTROLLER' AND NOT EXISTS (SELECT 1 FROM employees e WHERE e.job_role_id = job_roles.id)")
    op.execute("DELETE FROM roles WHERE code = 'EXAM_CONTROLLER' AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.role_id = roles.id)")