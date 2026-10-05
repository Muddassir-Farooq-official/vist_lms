"""assignments: teacher sets work, students submit a Google Drive link, teacher accepts or rejects

Revision ID: d6a2f8c1e305
Revises: c5f1a9d7e204
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d6a2f8c1e305"
down_revision = "c5f1a9d7e204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE notification_category ADD VALUE IF NOT EXISTS 'ASSIGNMENT'")
    op.create_table(
        "assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("course_offering_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assignments_offering", "assignments", ["course_offering_id"])
    op.create_table(
        "assignment_submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("drive_url", sa.String(500), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("status", postgresql.ENUM("SUBMITTED", "ACCEPTED", "REJECTED", name="submission_status"), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resubmitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resubmission_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("assignment_id", "student_id", name="uq_submission_assignment_student"),
    )
    op.create_index("ix_submissions_assignment", "assignment_submissions", ["assignment_id"])
    op.create_index("ix_submissions_student", "assignment_submissions", ["student_id"])

    # New permissions for existing installs (the seed script does the same for fresh ones):
    #   teachers manage assignments for their classes, students submit.
    for code, description, role in (
        ("assignment.manage", "Set assignments for my classes and review submissions", "TEACHER"),
        ("assignment.submit", "Submit work for my assignments", "STUDENT"),
    ):
        op.execute(f"""
            INSERT INTO permissions (id, code, description, created_at, updated_at)
            SELECT gen_random_uuid(), '{code}', '{description}', now(), now()
            WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = '{code}')
        """)
        op.execute(f"""
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT r.id, p.id FROM roles r, permissions p
            WHERE r.code = '{role}' AND p.code = '{code}'
              AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)
        """)


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('assignment.manage', 'assignment.submit'))")
    op.execute("DELETE FROM permissions WHERE code IN ('assignment.manage', 'assignment.submit')")
    op.drop_index("ix_submissions_student", table_name="assignment_submissions")
    op.drop_index("ix_submissions_assignment", table_name="assignment_submissions")
    op.drop_table("assignment_submissions")
    op.drop_index("ix_assignments_offering", table_name="assignments")
    op.drop_table("assignments")
    op.execute("DROP TYPE IF EXISTS submission_status")
    # (PostgreSQL cannot remove a value from the notification_category enum; it is left in place, unused.)