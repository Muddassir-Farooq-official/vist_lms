"""attendance: LATE/LEAVE statuses, class_sessions.attendance_taken_at, correction requests

Revision ID: c6a2e8d41f75
Revises: b5f1d3a7c962
"""
from alembic import op
import sqlalchemy as sa

revision = "c6a2e8d41f75"
down_revision = "b5f1d3a7c962"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on older
    # PostgreSQL, so step outside it just for these two statements.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE attendance_status ADD VALUE IF NOT EXISTS 'LATE'")
        op.execute("ALTER TYPE attendance_status ADD VALUE IF NOT EXISTS 'LEAVE'")

    op.add_column("class_sessions", sa.Column("attendance_taken_at", sa.DateTime(timezone=True), nullable=True))
    # Sessions that already have attendance rows were, by definition, taken.
    op.execute(
        """
        UPDATE class_sessions cs
        SET attendance_taken_at = sub.first_marked
        FROM (
            SELECT class_session_id, MIN(marked_at) AS first_marked
            FROM attendance_records GROUP BY class_session_id
        ) sub
        WHERE sub.class_session_id = cs.id
        """
    )

    op.create_table(
        "attendance_correction_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("class_session_id", sa.Uuid(), sa.ForeignKey("class_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("present_student_ids", sa.JSON(), nullable=False),
        sa.Column("late_student_ids", sa.JSON(), nullable=False),
        sa.Column("leave_student_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(500), nullable=True),
    )
    op.create_index("ix_attendance_correction_requests_status", "attendance_correction_requests", ["status"])
    op.create_index("ix_attendance_correction_requests_session", "attendance_correction_requests", ["class_session_id"])


def downgrade() -> None:
    op.drop_table("attendance_correction_requests")
    op.drop_column("class_sessions", "attendance_taken_at")
    # PostgreSQL cannot drop enum values; LATE/LEAVE stay in the type (harmless).