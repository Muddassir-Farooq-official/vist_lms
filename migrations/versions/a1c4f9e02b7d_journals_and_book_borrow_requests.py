"""journals (is_journal + periodical metadata on books) and book borrow requests

Revision ID: a1c4f9e02b7d
Revises: 132e1c4cbce1
Create Date: 2026-08-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'a1c4f9e02b7d'
down_revision = '132e1c4cbce1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Journals reuse the existing books/book_copies/book_issues tables —
    # a periodical is just a catalogue title with different metadata and
    # the same circulation model as a book (see app/library/models.py for
    # the reasoning). is_journal is what puts a title in the Journals
    # section instead of the general book catalogue.
    op.add_column('books', sa.Column('is_journal', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column('books', sa.Column('journal_volume', sa.String(length=30), nullable=True))
    op.add_column('books', sa.Column('journal_issue_number', sa.String(length=30), nullable=True))
    # server_default only needed to backfill existing rows; drop it so new
    # inserts must supply the value explicitly (matches every other
    # non-nullable column in this codebase — no silent defaults at the DB
    # layer beyond the one-time backfill).
    op.alter_column('books', 'is_journal', server_default=None)
    op.create_index('ix_books_is_journal', 'books', ['is_journal'], unique=False)

    op.create_table(
        'book_borrow_requests',
        sa.Column('book_id', sa.Uuid(), nullable=False),
        # postgresql.ENUM(..., create_type=False) — 'borrower_type' already
        # exists (created by the original library module migration,
        # 50a6caaf23ad) and is reused as-is here. The dialect-specific
        # postgresql.ENUM class is used rather than generic sa.Enum
        # because create_type=False is not reliably honored by generic
        # sa.Enum across SQLAlchemy versions — confirmed by this exact
        # migration raising DuplicateObjectError on a newer SQLAlchemy
        # (2.0.4x on Windows) despite working with generic sa.Enum on
        # 2.0.52 in testing. postgresql.ENUM is the documented, reliable
        # way to reference an existing enum type without recreating it.
        sa.Column('borrower_type', postgresql.ENUM('STUDENT', 'EMPLOYEE', name='borrower_type', create_type=False), nullable=False),
        sa.Column('borrower_id', sa.Uuid(), nullable=False),
        sa.Column('requested_by', sa.Uuid(), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('status', sa.Enum('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED', name='book_borrow_request_status'), nullable=False),
        sa.Column('decided_by', sa.Uuid(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decision_note', sa.String(length=255), nullable=True),
        sa.Column('resulting_issue_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['book_id'], ['books.id'], name=op.f('fk_book_borrow_requests_book_id_books'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['requested_by'], ['users.id'], name=op.f('fk_book_borrow_requests_requested_by_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_book_borrow_requests_decided_by_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['resulting_issue_id'], ['book_issues.id'], name=op.f('fk_book_borrow_requests_resulting_issue_id_book_issues'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_book_borrow_requests')),
    )
    op.create_index('ix_book_borrow_requests_book_id', 'book_borrow_requests', ['book_id'], unique=False)
    op.create_index('ix_book_borrow_requests_borrower', 'book_borrow_requests', ['borrower_type', 'borrower_id'], unique=False)
    op.create_index('ix_book_borrow_requests_status', 'book_borrow_requests', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_book_borrow_requests_status', table_name='book_borrow_requests')
    op.drop_index('ix_book_borrow_requests_borrower', table_name='book_borrow_requests')
    op.drop_index('ix_book_borrow_requests_book_id', table_name='book_borrow_requests')
    op.drop_table('book_borrow_requests')
    op.execute("DROP TYPE IF EXISTS book_borrow_request_status")

    op.drop_index('ix_books_is_journal', table_name='books')
    op.drop_column('books', 'journal_issue_number')
    op.drop_column('books', 'journal_volume')
    op.drop_column('books', 'is_journal')