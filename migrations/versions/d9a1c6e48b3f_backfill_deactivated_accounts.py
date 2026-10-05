"""backfill User.is_active for already-suspended/dropped/graduated students and terminated employees

Revision ID: d9a1c6e48b3f
Revises: c7e3f9a52d6b
Create Date: 2026-09-15 00:00:00.000000

Real bug fix, not a new feature: changing a student's or employee's
status has never actually deactivated their login account — only a
display flag (students.is_active_flag) was ever touched. This meant a
suspended, dropped, or graduated student — or a terminated employee —
could keep logging into the portal indefinitely.

app/students/service.py and app/employees/service.py now sync
users.is_active on every FUTURE status change; this migration is the
one-time backfill for every account that was ALREADY in one of these
states before the fix existed — without it, only status changes made
after this deploys would be caught, and every already-suspended/
dropped/graduated/terminated account would stay silently able to log
in until something changes their status again.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'd9a1c6e48b3f'
down_revision = 'c7e3f9a52d6b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE users
        SET is_active = false
        FROM students
        WHERE students.user_id = users.id
          AND students.status != 'ACTIVE'
        """
    )
    op.execute(
        """
        UPDATE users
        SET is_active = false
        FROM employees
        WHERE employees.user_id = users.id
          AND employees.status = 'TERMINATED'
        """
    )


def downgrade() -> None:
    # Not reversible — there is no record of whether a given account's
    # is_active=false was because of this backfill or a separate
    # deliberate deactivation. Same one-way-change class as every other
    # data-correction migration in this project.
    pass