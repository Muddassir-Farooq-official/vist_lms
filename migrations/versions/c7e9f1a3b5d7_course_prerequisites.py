"""Course prerequisites: a course can require passing another course first.

Revision ID: c7e9f1a3b5d7
Revises: b6d8f0a2c4e6
Create Date: 2026-11-07 00:00:01.000000

ADDITIVE and safe to run while the old version of the app is still serving:
- new, empty table course_prerequisites (nothing existing is copied or changed).
Until an administrator adds a prerequisite to a course, enrolment works exactly as before.
Downgrade drops the table (the prerequisites entered would be lost).
"""
from alembic import op
import sqlalchemy as sa

revision = "c7e9f1a3b5d7"
down_revision = "b6d8f0a2c4e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_prerequisites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("requires_course_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], name=op.f("fk_course_prerequisites_course_id_courses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requires_course_id"], ["courses.id"], name=op.f("fk_course_prerequisites_requires_course_id_courses"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_course_prerequisites_created_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_prerequisites")),
        sa.UniqueConstraint("course_id", "requires_course_id", name="uq_course_prerequisite_pair"),
        sa.CheckConstraint("course_id <> requires_course_id", name=op.f("ck_course_prerequisites_not_itself")),
    )
    op.create_index("ix_course_prerequisites_course", "course_prerequisites", ["course_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_course_prerequisites_course", table_name="course_prerequisites")
    op.drop_table("course_prerequisites")
