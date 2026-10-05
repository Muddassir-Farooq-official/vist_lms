"""course sections (section_label + capacity) and student section

Revision ID: b5f1d3a7c962
Revises: a4e7c2b95d18
"""
from alembic import op
import sqlalchemy as sa

revision = "b5f1d3a7c962"
down_revision = "a4e7c2b95d18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "course_offerings",
        sa.Column("section_label", sa.String(30), nullable=False, server_default="A"),
    )
    op.add_column("course_offerings", sa.Column("capacity", sa.Integer(), nullable=True))
    op.create_check_constraint("ck_course_offerings_positive_capacity", "course_offerings", "capacity IS NULL OR capacity > 0")
    op.drop_constraint("uq_course_offerings_course_term", "course_offerings", type_="unique")
    op.create_unique_constraint(
        "uq_course_offerings_course_term_section", "course_offerings", ["course_id", "term_label", "section_label"]
    )
    op.add_column("students", sa.Column("section_label", sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column("students", "section_label")
    op.drop_constraint("uq_course_offerings_course_term_section", "course_offerings", type_="unique")
    op.create_unique_constraint("uq_course_offerings_course_term", "course_offerings", ["course_id", "term_label"])
    op.drop_constraint("ck_course_offerings_positive_capacity", "course_offerings", type_="check")
    op.drop_column("course_offerings", "capacity")
    op.drop_column("course_offerings", "section_label")