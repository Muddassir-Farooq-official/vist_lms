"""Accounts office can bring in previous dues.

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-11-03 00:00:00.000000

Everything here is ADDITIVE and safe to run while the old version of the app is still serving:
- one new permission, fee.opening_balance.add, given to ADMIN and ACCOUNTS_OFFICER.
  It covers adding previous dues for one student and checking / importing a previous-dues CSV.
  Cancelling (writing off) a previous-dues entry still needs fee.manage (administrator) and a second administrator.
No existing row is changed or removed; no fee, ledger or payment record is touched.
The old code never checks the new permission, so it simply has no effect until the new code is running.
Accounts officers who are signed in pick it up when they sign in again (or their session refreshes).

Downgrade removes the permission and its role links again (previous dues already added stay, as they should).
"""
from alembic import op
import sqlalchemy as sa

revision = "e7f8a9b0c1d2"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None

CODE = "fee.opening_balance.add"
DESCRIPTION = "Bring in previous dues (one student or a checked CSV)"
ROLES = ["ADMIN", "ACCOUNTS_OFFICER"]


def upgrade() -> None:
    op.execute(sa.text(
        "INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
        "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=CODE, d=DESCRIPTION))
    for role in ROLES:
        op.execute(sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = :r AND p.code = :c "
            "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(r=role, c=CODE))


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = :c)").bindparams(c=CODE))
    op.execute(sa.text("DELETE FROM permissions WHERE code = :c").bindparams(c=CODE))