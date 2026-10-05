"""book short_code for human-readable per-book accession codes

Revision ID: d4e8b6f13a2c
Revises: c7f3a1d95e6b
Create Date: 2026-08-28 00:00:00.000000

Adds Book.short_code (BK-0001, BK-0002, ...) and backfills it for any
existing books. This is the missing piece that lets NEW copy accession
codes read as "<short_code>-C<seq>" (e.g. BK-0007-C003) instead of a
random 8-character hex fragment (BC-018D8FEF) — the old scheme was
collision-safe but not human-usable once a single title has dozens of
physical copies, which is exactly the situation this addresses.

Deliberately does NOT touch existing book_copies.accession_code values
— those may already be printed on physical barcode labels, so renaming
them would invalidate real-world labels. Only short_code is new;
existing copies keep their current accession codes exactly as they are,
and only copies added from this point forward use the new format.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e8b6f13a2c'
down_revision = 'c7f3a1d95e6b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('books', sa.Column('short_code', sa.String(length=20), nullable=True))

    op.execute(
        """
        WITH numbered AS (
            SELECT id, ROW_NUMBER() OVER (ORDER BY created_at) AS rn
            FROM books
            WHERE short_code IS NULL
        )
        UPDATE books
        SET short_code = 'BK-' || LPAD(numbered.rn::text, 4, '0')
        FROM numbered
        WHERE books.id = numbered.id
        """
    )

    op.alter_column('books', 'short_code', nullable=False)
    op.create_unique_constraint('uq_books_short_code', 'books', ['short_code'])

    # Seed the counter so future book creation continues past whatever
    # was just backfilled, using MAX rather than COUNT for the same
    # "correct even if something already coincidentally matched the
    # pattern" reasoning as the roll-number/employee-ID migration.
    op.execute(
        """
        WITH parsed AS (
            SELECT (regexp_match(short_code, '^BK-([0-9]{4})$'))[1]::int AS seq
            FROM books
            WHERE short_code ~ '^BK-[0-9]{4}$'
        )
        INSERT INTO sequence_counters (id, scope_key, next_value)
        SELECT gen_random_uuid(), 'book_short_code', COALESCE(MAX(seq), 0) + 1
        FROM parsed
        ON CONFLICT (scope_key) DO UPDATE
            SET next_value = GREATEST(sequence_counters.next_value, EXCLUDED.next_value)
        """
    )


def downgrade() -> None:
    op.drop_constraint('uq_books_short_code', 'books', type_='unique')
    op.drop_column('books', 'short_code')