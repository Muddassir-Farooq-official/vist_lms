"""renewal_count on book_issues + WAITLISTED borrow-request status

Revision ID: a3c6e9f21b4d
Revises: f8b3d7e29a1c
Create Date: 2026-08-28 00:00:00.000000

Two independent additions:
1. book_issues.renewal_count — tracks self-service renewals (capped in
   app code, see IssueService.renew()).
2. A new WAITLISTED value on the existing book_borrow_request_status
   enum — a request auto-lands here instead of failing outright when
   approved but no copy is free; auto-fulfilled FIFO the next time a
   copy of that book is returned.

ALTER TYPE ... ADD VALUE cannot be combined with other DDL in the same
transaction in older Postgres, but as a single statement in its own
migration transaction it's fine — confirmed by actually running this
against a real database (see CHANGELOG), not just written and assumed.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3c6e9f21b4d'
down_revision = 'f8b3d7e29a1c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('book_issues', sa.Column('renewal_count', sa.Integer(), server_default='0', nullable=False))
    op.alter_column('book_issues', 'renewal_count', server_default=None)

    op.execute("ALTER TYPE book_borrow_request_status ADD VALUE IF NOT EXISTS 'WAITLISTED'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums — removing 'WAITLISTED' would
    # require rebuilding the type from scratch (create new type, cast
    # column, drop old type), and only makes sense if you're certain no
    # row uses it. Not attempted here; this downgrade only reverts the
    # renewal_count column.
    op.drop_column('book_issues', 'renewal_count')