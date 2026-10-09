"""supplementary: reappear-exam attempts (supplementary_attempts) and the 'supplementary' flag on enrollment_results

Revision ID: fb1f2a3b4c5d
Revises: fa0e1f2a3b4c
"""
import sqlalchemy as sa
from alembic import op

revision = "fb1f2a3b4c5d"
down_revision = "fa0e1f2a3b4c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("enrollment_results", sa.Column("supplementary", sa.Boolean(), nullable=False, server_default="false"))
    status = sa.Enum("REGISTERED", "MARKED", "PUBLISHED", name="supplementary_status")
    op.create_table(
        "supplementary_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("enrollment_id", sa.Uuid(), sa.ForeignKey("enrollments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("offering_assessment_id", sa.Uuid(), sa.ForeignKey("offering_assessments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("obtained_marks", sa.Numeric(6, 2), nullable=True),
        sa.Column("absent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("original_result", sa.JSON(), nullable=False),
        sa.Column("new_result", sa.JSON(), nullable=True),
        sa.Column("registered_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("marked_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(300), nullable=True),
        sa.UniqueConstraint("enrollment_id", name="uq_supplementary_attempts_enrollment"),
    )


def downgrade() -> None:
    op.drop_table("supplementary_attempts")
    sa.Enum(name="supplementary_status").drop(op.get_bind(), checkfirst=True)
    op.drop_column("enrollment_results", "supplementary")