"""Graded attendance fines: one row per student and course that is short of attendance.

Revision ID: b6d8f0a2c4e6
Revises: a5c7e9f1b3d5
Create Date: 2026-11-07 00:00:00.000000

ADDITIVE and safe to run while the old version of the app is still serving:
- new, empty table attendance_shortfalls (nothing existing is copied or changed).
No existing row of any table is modified; fines already issued stay exactly as they are.
Downgrade drops the table (the fines it charged stay in the fines table; only the link to the band is lost).
"""
from alembic import op
import sqlalchemy as sa

revision = "b6d8f0a2c4e6"
down_revision = "a5c7e9f1b3d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attendance_shortfalls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("course_offering_id", sa.Uuid(), nullable=False),
        sa.Column("term_label", sa.String(length=50), nullable=False),
        sa.Column("percentage", sa.Numeric(5, 2), nullable=False),
        sa.Column("band", sa.Integer(), nullable=False),
        sa.Column("amount_charged", sa.Numeric(10, 2), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("decision_note", sa.String(length=500), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], name=op.f("fk_attendance_shortfalls_student_id_students"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["course_offering_id"], ["course_offerings.id"], name=op.f("fk_attendance_shortfalls_course_offering_id_course_offerings"),
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], name=op.f("fk_attendance_shortfalls_decided_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attendance_shortfalls")),
        sa.UniqueConstraint("student_id", "course_offering_id", name="uq_attendance_shortfall_student_offering"),
    )
    op.create_index("ix_attendance_shortfalls_status", "attendance_shortfalls", ["status"], unique=False)
    op.create_index("ix_attendance_shortfalls_term", "attendance_shortfalls", ["term_label"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_attendance_shortfalls_term", table_name="attendance_shortfalls")
    op.drop_index("ix_attendance_shortfalls_status", table_name="attendance_shortfalls")
    op.drop_table("attendance_shortfalls")
