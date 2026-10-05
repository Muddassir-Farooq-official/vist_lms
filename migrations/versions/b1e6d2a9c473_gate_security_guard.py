"""gate: a Security Guard role that marks students and staff in and out; timings set by the administration; late-arrival fines for students

Revision ID: b1e6d2a9c473
Revises: a9d5c1f4b638
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b1e6d2a9c473"
down_revision = "a9d5c1f4b638"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE fine_source ADD VALUE IF NOT EXISTS 'GATE'")

    op.create_table(
        "gate_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_in_time", sa.Time(), nullable=False, server_default="08:30"),
        sa.Column("student_out_time", sa.Time(), nullable=True),
        sa.Column("student_grace_minutes", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("student_days", sa.String(20), nullable=False, server_default="0,1,2,3,4"),
        sa.Column("student_fine_per_hour", sa.Numeric(10, 2), nullable=False, server_default="100"),
        sa.Column("student_max_fine_per_day", sa.Numeric(10, 2), nullable=True),
        sa.Column("student_auto_fine", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("staff_default_in", sa.Time(), nullable=True, server_default="09:00"),
        sa.Column("staff_default_out", sa.Time(), nullable=True, server_default="17:00"),
        sa.Column("staff_grace_minutes", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("staff_default_days", sa.String(20), nullable=False, server_default="0,1,2,3,4"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("INSERT INTO gate_settings (id) VALUES (1)")

    op.create_table(
        "gate_employee_schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("in_time", sa.Time(), nullable=False),
        sa.Column("out_time", sa.Time(), nullable=False),
        sa.Column("working_days", sa.String(20), nullable=False, server_default="0,1,2,3,4"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "gate_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("log_date", sa.Date(), nullable=False),
        sa.Column("check_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_in", sa.Time(), nullable=True),
        sa.Column("expected_out", sa.Time(), nullable=True),
        sa.Column("grace_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_working_day", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("late_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("early_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("in_marked_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("out_marked_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("fine_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fines.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "person_id", "log_date", name="uq_gate_logs_person_day"),
    )
    op.create_index("ix_gate_logs_date", "gate_logs", ["log_date"])

    # The Security Guard role, its permissions, and a job title so the administration can create guards like any other employee.
    op.execute(sa.text("INSERT INTO roles (id, code, name, created_at, updated_at) SELECT gen_random_uuid(), 'SECURITY_GUARD', 'Security Guard', now(), now() "
                       "WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'SECURITY_GUARD')"))
    op.execute(sa.text("INSERT INTO job_roles (id, code, title, default_role_code, is_active, created_at, updated_at) SELECT gen_random_uuid(), 'SECURITY_GUARD', 'Security Guard', 'SECURITY_GUARD', true, now(), now() "
                       "WHERE NOT EXISTS (SELECT 1 FROM job_roles WHERE code = 'SECURITY_GUARD')"))
    grants = [("gate.operate", "Mark students and staff in and out at the gate", ["SECURITY_GUARD", "ADMIN"]),
              ("gate.manage", "Set gate timings and rules, correct the log and decide late-arrival fines", ["ADMIN"]),
              ("application.submit", "Submit applications (leave, certificates...) to the administration", ["SECURITY_GUARD"]),
              ("task.use", "Assign tasks to other staff and respond to tasks assigned to me", ["SECURITY_GUARD"])]
    for code, description, roles in grants:
        op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
                           "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=description))
        for role in roles:
            op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = :r AND p.code = :c "
                               "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(r=role, c=code))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE code = 'SECURITY_GUARD')")
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('gate.operate', 'gate.manage'))")
    op.execute("DELETE FROM permissions WHERE code IN ('gate.operate', 'gate.manage')")
    op.execute("DELETE FROM job_roles WHERE code = 'SECURITY_GUARD' AND NOT EXISTS (SELECT 1 FROM employees e WHERE e.job_role_id = job_roles.id)")
    op.execute("DELETE FROM roles WHERE code = 'SECURITY_GUARD' AND NOT EXISTS (SELECT 1 FROM user_roles ur WHERE ur.role_id = roles.id)")
    op.drop_index("ix_gate_logs_date", table_name="gate_logs")
    op.drop_table("gate_logs")
    op.drop_table("gate_employee_schedules")
    op.drop_table("gate_settings")
    # (PostgreSQL cannot remove a value from the fine_source enum; GATE is left in place, unused.)