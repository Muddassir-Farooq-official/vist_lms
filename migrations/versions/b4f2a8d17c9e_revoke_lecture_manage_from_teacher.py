"""revoke lecture.manage from teacher — syllabus/lecture uploads are admin-only

Revision ID: b4f2a8d17c9e
Revises: a1c9e7f43d2b
Create Date: 2026-09-15 00:00:00.000000

Approved decision: uploading/replacing/removing the course syllabus and
lecture resources (notes/slides) is an ADMIN-only action now — a
teacher can VIEW everything for their own assigned offerings, but not
edit any of it. app/auth/permissions.py has already been updated to
stop granting lecture.manage to TEACHER going forward; this migration
is the data-side fix for any database where the earlier (broader) grant
already landed — the seed script is purely additive and can never
revoke an over-grant like this on its own (same fix pattern as
c9e1a5d47f2b, which did the same thing for fine.issue).
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b4f2a8d17c9e'
down_revision = 'a1c9e7f43d2b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE role_id IN (SELECT id FROM roles WHERE code = 'TEACHER')
          AND permission_id = (SELECT id FROM permissions WHERE code = 'lecture.manage')
        """
    )


def downgrade() -> None:
    # Deliberately not restoring the old grant — this was an approved
    # governance change, not a bug being reverted. Nothing to do here.
    pass