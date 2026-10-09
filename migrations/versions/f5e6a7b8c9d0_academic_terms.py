"""academics: the institute's list of academic terms (label, dates, open/closed)

Back-fills one term for every label already used by course offerings or fee
structures, with the earliest start / latest end date seen. Terms whose dates
are over are CLOSED; IMPORTED (past-results) terms are CLOSED.

Revision ID: f5e6a7b8c9d0
Revises: f4d5e6a7b8c9
"""
import sqlalchemy as sa
from alembic import op

revision = "f5e6a7b8c9d0"
down_revision = "f4d5e6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    status = sa.Enum("OPEN", "CLOSED", name="academic_term_status")
    op.create_table(
        "academic_terms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("label", sa.String(50), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("status", status, nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("label", name="uq_academic_terms_label"),
    )
    op.execute("""
        INSERT INTO academic_terms (id, label, start_date, end_date, status, created_at)
        SELECT gen_random_uuid(), label, MIN(start_date), MAX(end_date),
               CASE WHEN bool_and(imported) OR MAX(end_date) < CURRENT_DATE THEN 'CLOSED'::academic_term_status
                    ELSE 'OPEN'::academic_term_status END,
               now()
        FROM (
            SELECT term_label AS label, term_start_date AS start_date, term_end_date AS end_date, section_label = 'IMPORTED' AS imported
            FROM course_offerings
            UNION ALL
            SELECT term_label, NULL, NULL, false FROM fee_structures
        ) used
        GROUP BY label
    """)


def downgrade() -> None:
    op.drop_table("academic_terms")
    sa.Enum(name="academic_term_status").drop(op.get_bind(), checkfirst=True)