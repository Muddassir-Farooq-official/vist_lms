"""revoke stray fee.manage grant from accounts_officer role

Revision ID: 0858d901dd42
Revises: ebb6a9fe36a2
Create Date: 2026-08-24 06:59:02.064921

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0858d901dd42'
down_revision = 'ebb6a9fe36a2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Fixes a real permission-governance bug: ACCOUNTS_OFFICER should
    never have fee.manage (fee types/structures are ADMIN-only per
    approved governance — the code in app/auth/permissions.py has always
    correctly excluded it), but a stale role_permission row granting it
    existed in the database, unrelated to any code path — the seed
    script is purely additive and can never revoke an over-grant like
    this on its own. Caught by actually logging in AS an Accounts
    Officer and attempting to create a fee type, which is the only way
    this class of bug ever surfaces. Data-only fix, safe to run even if
    the stray row doesn't exist in a given environment (DELETE matching
    zero rows is a no-op)."""
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE role_id = (SELECT id FROM roles WHERE code = 'ACCOUNTS_OFFICER')
          AND permission_id = (SELECT id FROM permissions WHERE code = 'fee.manage')
        """
    )


def downgrade() -> None:
    # Deliberately not restoring the over-grant on downgrade — it was a
    # bug, not an intentional piece of schema/data this migration is
    # responsible for reverting. Nothing to do here.
    pass