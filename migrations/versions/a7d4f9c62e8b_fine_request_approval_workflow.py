"""fine request/approval workflow — librarian and accounts officer request, admin approves

Revision ID: a7d4f9c62e8b
Revises: e5b7d3a91c6f
Create Date: 2026-09-17 00:00:00.000000

Approved decision: LIBRARIAN and ACCOUNTS_OFFICER may no longer issue a
Fine directly (FINE_ISSUE has been ADMIN-only for a while now, per an
earlier migration) — they instead REQUEST one via the new FINE_REQUEST
permission, restricted to their own domain (librarian: LIBRARY source
only; accounts officer: OTHER source only). The request sits as
PENDING_APPROVAL — invisible to the payer, not chargeable, not payable
— until an admin approves it into a real PENDING fine, or rejects it.
ADMIN's existing direct-issue path (FineService.issue) is unaffected:
an admin-issued fine is immediately its own approval (approved_by/
approved_at set to the same actor/time as issued_by/issued_at), no
separate review step for admin's own action.

Same proven combination as e5b7d3a91c6f (add_column + ALTER TYPE ...
ADD VALUE in one migration, no autocommit_block needed in this
environment).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7d4f9c62e8b'
down_revision = 'e5b7d3a91c6f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('fines', sa.Column('approved_by', sa.Uuid(), nullable=True))
    op.add_column('fines', sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        'fk_fines_approved_by_users', 'fines', 'users', ['approved_by'], ['id'], ondelete='SET NULL'
    )

    # Every fine that has ever been chargeable (i.e. currently PENDING,
    # or already PAID/WAIVED) was, by definition, issued directly by an
    # admin under the old always-immediate-issue behavior — backfill
    # approved_by/approved_at from issued_by/issued_at so historical
    # rows aren't left looking unapproved under the new model.
    op.execute(
        """
        UPDATE fines
        SET approved_by = issued_by, approved_at = issued_at
        WHERE status IN ('PENDING', 'PAID', 'WAIVED')
        """
    )

    op.execute("ALTER TYPE fine_status ADD VALUE IF NOT EXISTS 'PENDING_APPROVAL'")
    op.execute("ALTER TYPE fine_status ADD VALUE IF NOT EXISTS 'REJECTED'")


def downgrade() -> None:
    op.drop_constraint('fk_fines_approved_by_users', 'fines', type_='foreignkey')
    op.drop_column('fines', 'approved_at')
    op.drop_column('fines', 'approved_by')
    # Not removing the two enum values — Postgres has no ALTER TYPE ...
    # DROP VALUE, and any fine already in PENDING_APPROVAL or REJECTED
    # would become unreadable if we tried to work around that. Same
    # one-way-change class as every other enum addition in this project.