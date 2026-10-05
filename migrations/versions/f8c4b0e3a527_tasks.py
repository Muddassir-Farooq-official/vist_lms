"""tasks: staff assign work to each other with a deadline; accept / reject with a reason; flag for the administration; track progress

Revision ID: f8c4b0e3a527
Revises: e7b3a9d2f416
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f8c4b0e3a527"
down_revision = "e7b3a9d2f416"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE notification_category ADD VALUE IF NOT EXISTS 'TASK'")
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("serial_no", sa.Integer(), sa.Identity(), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", postgresql.ENUM("LOW", "NORMAL", "HIGH", "URGENT", name="task_priority"), nullable=False),
        sa.Column("assigner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("assignee_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", postgresql.ENUM("PENDING", "ACCEPTED", "REJECTED", "COMPLETED", "CANCELLED", name="task_status"), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extension_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", postgresql.ENUM("NO_CAPACITY", "NOT_MY_RESPONSIBILITY", "DEADLINE_TOO_SHORT", "MISSING_INFORMATION", "CONFLICTS_WITH_OTHER_WORK", "OTHER", name="task_reject_reason"), nullable=True),
        sa.Column("reject_explanation", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_note", sa.Text(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("follow_up_of_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, col in (("ix_tasks_assignee", "assignee_user_id"), ("ix_tasks_assigner", "assigner_user_id"), ("ix_tasks_status", "status"), ("ix_tasks_deadline", "deadline")):
        op.create_index(name, "tasks", [col])
    op.create_table(
        "task_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_comments_task", "task_comments", ["task_id"])
    op.create_table(
        "task_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("flagged_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", postgresql.ENUM("OPEN", "RESOLVED", name="task_flag_status"), nullable=False),
        sa.Column("outcome", postgresql.ENUM("NO_ACTION", "TASK_CANCELLED", "DEADLINE_EXTENDED", name="task_flag_outcome"), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_flags_task", "task_flags", ["task_id"])
    op.create_index("ix_task_flags_status", "task_flags", ["status"])

    # Permissions for existing installs (the seed script does the same for fresh ones).
    grants = [("task.use", "Assign tasks to other staff and respond to tasks assigned to me", ["ADMIN", "TEACHER", "LIBRARIAN", "ACCOUNTS_OFFICER", "HOSTEL_WARDEN"]),
              ("task.manage", "See every task, resolve flagged tasks and view staff progress reports", ["ADMIN"])]
    for code, description, roles in grants:
        op.execute(sa.text(
            "INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
            "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=description))
        for role in roles:
            op.execute(sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = :r AND p.code = :c "
                "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(r=role, c=code))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('task.use', 'task.manage'))")
    op.execute("DELETE FROM permissions WHERE code IN ('task.use', 'task.manage')")
    for name in ("ix_task_flags_status", "ix_task_flags_task"):
        op.drop_index(name, table_name="task_flags")
    op.drop_table("task_flags")
    op.drop_index("ix_task_comments_task", table_name="task_comments")
    op.drop_table("task_comments")
    for name in ("ix_tasks_deadline", "ix_tasks_status", "ix_tasks_assigner", "ix_tasks_assignee"):
        op.drop_index(name, table_name="tasks")
    op.drop_table("tasks")
    for name in ("task_flag_outcome", "task_flag_status", "task_reject_reason", "task_status", "task_priority"):
        op.execute(f"DROP TYPE IF EXISTS {name}")
    # (PostgreSQL cannot remove a value from the notification_category enum; it is left in place, unused.)