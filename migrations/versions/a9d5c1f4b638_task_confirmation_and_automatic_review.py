"""tasks: the assigner confirms completion; every rejection goes to the administration automatically

Revision ID: a9d5c1f4b638
Revises: f8c4b0e3a527
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a9d5c1f4b638"
down_revision = "f8c4b0e3a527"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'AWAITING_CONFIRMATION'")
    op.execute("ALTER TYPE task_flag_outcome ADD VALUE IF NOT EXISTS 'REJECTION_OVERRULED'")
    op.add_column("tasks", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("tasks", sa.Column("rework_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("tasks", sa.Column("sent_back_note", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("rejection_overruled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("task_flags", sa.Column("is_automatic", sa.Boolean(), nullable=False, server_default=sa.false()))
    # Tasks completed before confirmation existed count as confirmed.
    op.execute("UPDATE tasks SET confirmed_at = completed_at WHERE status = 'COMPLETED' AND confirmed_at IS NULL")


def downgrade() -> None:
    op.drop_column("task_flags", "is_automatic")
    for col in ("rejection_overruled_at", "sent_back_note", "rework_count", "confirmed_by", "confirmed_at"):
        op.drop_column("tasks", col)
    # (PostgreSQL cannot remove values from an enum; AWAITING_CONFIRMATION and REJECTION_OVERRULED are left in place, unused.)