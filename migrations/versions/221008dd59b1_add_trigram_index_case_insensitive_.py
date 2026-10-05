"""add trigram index, case-insensitive program uniqueness, cnic check constraints

Revision ID: 221008dd59b1
Revises: 95aa84ae9878
Create Date: 2026-08-19 05:45:24.246558

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '221008dd59b1'
down_revision = '95aa84ae9878'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Trigram search support (Section 44: no blind LIKE '%x%' on millions
    # of rows). Requires the pg_trgm extension.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Replace the plain btree placeholder index with real GIN trigram
    # indexes on the two name columns so ILIKE '%query%' searches use an
    # index scan instead of a sequential scan at scale.
    op.drop_index("ix_students_name_trgm", table_name="students")
    op.execute(
        "CREATE INDEX ix_students_first_name_trgm ON students USING gin (first_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_students_last_name_trgm ON students USING gin (last_name gin_trgm_ops)"
    )

    # --- Case-insensitive uniqueness on program name (Section 9/11) ---
    op.drop_index("ix_programs_name_lower", table_name="programs")
    op.execute(
        "CREATE UNIQUE INDEX uq_programs_name_lower ON programs (lower(trim(name)))"
    )

    # --- CNIC format check constraints (defense-in-depth alongside the
    # application-layer regex in app/imports/validators.py — Section 10/57:
    # never rely on a single validation layer). Format: 5 digits, 7 digits,
    # 1 digit, optionally dash-separated.
    op.create_check_constraint(
        "ck_students_cnic_format",
        "students",
        r"cnic ~ '^[0-9]{5}-[0-9]{7}-[0-9]{1}$'",
    )
    op.create_check_constraint(
        "ck_student_guardians_cnic_format",
        "student_guardians",
        r"cnic ~ '^[0-9]{5}-[0-9]{7}-[0-9]{1}$'",
    )

    # --- Case-insensitive uniqueness on username (auth) ---
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.execute("CREATE UNIQUE INDEX uq_users_username_lower ON users (lower(username))")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_users_username_lower")
    op.create_unique_constraint("uq_users_username", "users", ["username"])

    op.drop_constraint("ck_student_guardians_cnic_format", "student_guardians", type_="check")
    op.drop_constraint("ck_students_cnic_format", "students", type_="check")

    op.execute("DROP INDEX IF EXISTS uq_programs_name_lower")
    op.create_index("ix_programs_name_lower", "programs", ["name"])

    op.execute("DROP INDEX IF EXISTS ix_students_last_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_students_first_name_trgm")
    op.create_index("ix_students_name_trgm", "students", ["first_name", "last_name"])
