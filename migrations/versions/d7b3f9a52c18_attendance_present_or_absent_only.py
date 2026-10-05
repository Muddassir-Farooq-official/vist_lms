"""attendance is Present or Absent only: drop the Late/Leave leftovers

Revision ID: d7b3f9a52c18
Revises: c6a2e8d41f75

Module 4 briefly introduced LATE / LEAVE statuses. The institute's rule is
that attendance has exactly two options, so:
  * any rows already saved as LATE become PRESENT, LEAVE become ABSENT
    (a no-op on a database where they were never used);
  * the two extra list columns on correction requests are dropped.
The two extra values stay inside the PostgreSQL enum type (PostgreSQL cannot
remove enum values); nothing reads or writes them any more.
"""
from alembic import op
import sqlalchemy as sa

revision = "d7b3f9a52c18"
down_revision = "c6a2e8d41f75"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE attendance_records SET status = 'PRESENT' WHERE status::text = 'LATE'")
    op.execute("UPDATE attendance_records SET status = 'ABSENT' WHERE status::text = 'LEAVE'")
    op.execute("ALTER TABLE attendance_correction_requests DROP COLUMN IF EXISTS late_student_ids")
    op.execute("ALTER TABLE attendance_correction_requests DROP COLUMN IF EXISTS leave_student_ids")


def downgrade() -> None:
    op.add_column("attendance_correction_requests", sa.Column("late_student_ids", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("attendance_correction_requests", sa.Column("leave_student_ids", sa.JSON(), nullable=False, server_default="[]"))