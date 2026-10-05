"""results: the grading scale (letter grades and grade points) and each student's frozen result for every course

Revision ID: d3a8f6b2c159
Revises: c2f7e5a1d948
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d3a8f6b2c159"
down_revision = "c2f7e5a1d948"
branch_labels = None
depends_on = None

# The widely used 4.0 scale with plus/minus grades (as recommended for Pakistani universities). Editable by the administration afterwards.
STANDARD_SCALE = [("A", 85, 4.00, True), ("A-", 80, 3.66, True), ("B+", 75, 3.33, True), ("B", 71, 3.00, True), ("B-", 68, 2.66, True),
                  ("C+", 64, 2.33, True), ("C", 61, 2.00, True), ("C-", 58, 1.66, True), ("D+", 54, 1.33, True), ("D", 50, 1.00, True), ("F", 0, 0.00, False)]


def upgrade() -> None:
    op.create_table(
        "grade_scale_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("letter", sa.String(4), nullable=False),
        sa.Column("min_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("grade_points", sa.Numeric(3, 2), nullable=False),
        sa.Column("is_passing", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("letter", name="uq_grade_scale_letter"),
        sa.UniqueConstraint("min_percent", name="uq_grade_scale_min_percent"),
    )
    for letter, minimum, points, passing in STANDARD_SCALE:
        op.execute(sa.text("INSERT INTO grade_scale_entries (id, letter, min_percent, grade_points, is_passing, created_at, updated_at) "
                           "VALUES (gen_random_uuid(), :l, :m, :p, :ok, now(), now())").bindparams(l=letter, m=minimum, p=points, ok=passing))
    op.create_table(
        "enrollment_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("enrollment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("enrollments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("percentage", sa.Numeric(5, 2), nullable=False),
        sa.Column("total_obtained", sa.Numeric(8, 2), nullable=False),
        sa.Column("total_max", sa.Numeric(8, 2), nullable=False),
        sa.Column("letter_grade", sa.String(4), nullable=False),
        sa.Column("grade_points", sa.Numeric(3, 2), nullable=False),
        sa.Column("credit_hours", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(10), nullable=False),
        sa.Column("attendance_percentage", sa.Numeric(5, 2), nullable=True),
        sa.Column("failed_for_attendance", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("enrollment_id", name="uq_enrollment_results_enrollment"),
    )
    # (Results published before this existed get their grade the first time a transcript is opened.)


def downgrade() -> None:
    op.drop_table("enrollment_results")
    op.drop_table("grade_scale_entries")