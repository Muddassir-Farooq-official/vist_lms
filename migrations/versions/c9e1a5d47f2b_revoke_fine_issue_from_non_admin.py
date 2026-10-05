"""revoke fine.issue from librarian, accounts_officer, teacher — admin only

Revision ID: c9e1a5d47f2b
Revises: b7d2f4a83e9c
Create Date: 2026-08-29 00:00:00.000000

Approved decision: only ADMIN issues fines now — Librarian, Accounts
Officer, and Teacher all keep fine.view (and Accounts Officer keeps
fine.manage, to resolve/mark-paid fines someone else issued), but none
of them can originate a new charge anymore. app/auth/permissions.py has
already been updated to stop granting fine.issue to these three roles
going forward; this migration is the data-side fix for any database
where the earlier grant already landed — the seed script is purely
additive and can never revoke an over-grant like this on its own (same
class of fix as migration 0858d901dd42).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c9e1a5d47f2b'
down_revision = 'b7d2f4a83e9c'
branch_labels = None
depends_on = None

REVOKE_FROM_ROLES = ('LIBRARIAN', 'ACCOUNTS_OFFICER', 'TEACHER')


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE role_id IN (SELECT id FROM roles WHERE code IN ('LIBRARIAN', 'ACCOUNTS_OFFICER', 'TEACHER'))
          AND permission_id = (SELECT id FROM permissions WHERE code = 'fine.issue')
        """
    )


def downgrade() -> None:
    # Deliberately not restoring the old grants — this was an approved
    # governance change, not a bug being reverted. Nothing to do here.
    pass