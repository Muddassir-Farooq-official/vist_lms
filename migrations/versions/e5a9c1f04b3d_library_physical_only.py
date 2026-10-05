"""library books are physical-only — drop digital edition/upload columns

Revision ID: e5a9c1f04b3d
Revises: d4e8b6f13a2c
Create Date: 2026-08-28 00:00:00.000000

Approved decision: the library catalogue never supported a working
digital-file workflow in the admin UI (the upload/URL fields existed in
the HTML form but were never wired up in the JS — a separate bug fixed
alongside this), and the decision going forward is physical-only, no
digital/PDF/upload concept at all. cover_image_url is untouched — it's
just a catalogue thumbnail, not "digital access" to the book.

WARNING — this is a real, one-way data deletion, not just a code
cleanup: if any existing book already had edition_type=DIGITAL/BOTH
with a digital_file_url set, or an uploaded PDF (digital_file_storage_key),
that information is dropped along with the columns. Uploaded PDF files
themselves are NOT deleted from disk by this migration (they live under
STORAGE_LOCAL_PATH/library/<book_id>/, outside the database) — if you
need to preserve any, copy them out before running this, since the
database's only record of which file belongs to which book is being
removed here.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5a9c1f04b3d'
down_revision = 'd4e8b6f13a2c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column('books', 'digital_file_original_name')
    op.drop_column('books', 'digital_file_storage_key')
    op.drop_column('books', 'digital_file_url')
    op.drop_column('books', 'edition_type')
    op.execute("DROP TYPE IF EXISTS book_edition_type")


def downgrade() -> None:
    book_edition_type = sa.Enum('PHYSICAL', 'DIGITAL', 'BOTH', name='book_edition_type')
    book_edition_type.create(op.get_bind(), checkfirst=True)
    op.add_column(
        'books',
        sa.Column(
            'edition_type', sa.Enum('PHYSICAL', 'DIGITAL', 'BOTH', name='book_edition_type', create_type=False),
            nullable=False, server_default='PHYSICAL',
        ),
    )
    op.alter_column('books', 'edition_type', server_default=None)
    op.add_column('books', sa.Column('digital_file_url', sa.String(length=1000), nullable=True))
    op.add_column('books', sa.Column('digital_file_storage_key', sa.String(length=255), nullable=True))
    op.add_column('books', sa.Column('digital_file_original_name', sa.String(length=255), nullable=True))
    # NOTE: values are NOT restored — this only recreates the columns,
    # all NULL/PHYSICAL. The data deleted by upgrade() is genuinely gone;
    # restore from a backup taken before upgrade() if you need it back.