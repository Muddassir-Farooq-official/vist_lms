"""system-generated roll numbers / employee IDs as login usernames

Revision ID: c7f3a1d95e6b
Revises: a1c4f9e02b7d
Create Date: 2026-08-28 00:00:00.000000

Approved decision: student roll numbers and employee IDs are now
system-generated (sequential per program/year for roll numbers, a global
sequence for employee IDs — see app/core/sequences.py) rather than
admin-supplied, and each one doubles as that person's permanent login
username (replacing the old std-<cnic> / staff-<cnic> scheme).

This migration:
  1. Creates `sequence_counters`, the generic counter table the new
     generator functions read/write.
  2. Adds `employees.employee_code`.
  3. Backfills employee_code for any existing employees (EMP-0001,
     EMP-0002, ... in creation order) and renames their linked user's
     username to match, for any username still following the old
     'staff-<cnic>' pattern.
  4. Backfills students.roll_number for any existing NULL rows
     (<PROGRAM_CODE or GEN>-<enrollment/creation year>-<seq>, sequential
     per program+year in creation order) and renames their linked
     user's username to match, for any username still following the old
     'std-<cnic>' pattern.
  5. Seeds sequence_counters so future generation continues past
     whatever was just backfilled, rather than colliding with it.
  6. Makes employees.employee_code and students.roll_number NOT NULL.

IMPORTANT — operational note, not something this migration can do for
you: every existing student/employee account's login username changes
as a result of step 3/4 above (unless it was already NULL/never
matched the old pattern). Anyone with already-issued credentials needs
their new username communicated to them; the password is unchanged.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c7f3a1d95e6b'
down_revision = 'a1c4f9e02b7d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. sequence_counters — generic race-safe counter table
    # ------------------------------------------------------------------
    op.create_table(
        'sequence_counters',
        sa.Column('scope_key', sa.String(length=150), nullable=False),
        sa.Column('next_value', sa.Integer(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_sequence_counters')),
        sa.UniqueConstraint('scope_key', name='uq_sequence_counters_scope_key'),
    )

    # ------------------------------------------------------------------
    # 2. employees.employee_code — added nullable first so it can be
    #    backfilled row-by-row before the NOT NULL constraint lands.
    # ------------------------------------------------------------------
    op.add_column('employees', sa.Column('employee_code', sa.String(length=20), nullable=True))

    # 3a. Backfill: EMP-0001, EMP-0002, ... in creation order.
    op.execute(
        """
        WITH numbered AS (
            SELECT id, ROW_NUMBER() OVER (ORDER BY created_at) AS rn
            FROM employees
            WHERE employee_code IS NULL
        )
        UPDATE employees
        SET employee_code = 'EMP-' || LPAD(numbered.rn::text, 4, '0')
        FROM numbered
        WHERE employees.id = numbered.id
        """
    )

    # 3b. Rename the linked login username to match, but only for
    #     accounts still on the old 'staff-<cnic>' pattern — never
    #     touch a username that was already customized some other way.
    op.execute(
        """
        UPDATE users u
        SET username = e.employee_code
        FROM employees e
        WHERE u.id = e.user_id AND u.username LIKE 'staff-%'
        """
    )

    op.alter_column('employees', 'employee_code', nullable=False)
    op.create_unique_constraint('uq_employees_employee_code', 'employees', ['employee_code'])

    # 4a. Backfill students.roll_number for any existing NULL rows —
    #     <PROGRAM_CODE or GEN>-<year>-<seq>, sequential per program+year
    #     using enrollment_date's year if set, else the row's creation
    #     year, matching app/core/sequences.py's own fallback logic.
    op.execute(
        """
        WITH numbered AS (
            SELECT s.id,
                   COALESCE(p.code, 'GEN') AS prog_code,
                   COALESCE(EXTRACT(YEAR FROM s.enrollment_date), EXTRACT(YEAR FROM s.created_at))::int AS yr,
                   ROW_NUMBER() OVER (
                       PARTITION BY COALESCE(p.code, 'GEN'),
                                    COALESCE(EXTRACT(YEAR FROM s.enrollment_date), EXTRACT(YEAR FROM s.created_at))
                       ORDER BY s.created_at
                   ) AS rn
            FROM students s
            LEFT JOIN programs p ON p.id = s.program_id
            WHERE s.roll_number IS NULL
        )
        UPDATE students
        SET roll_number = numbered.prog_code || '-' || numbered.yr || '-' || LPAD(numbered.rn::text, 4, '0')
        FROM numbered
        WHERE students.id = numbered.id
        """
    )

    # 4b. Same username-rename discipline as employees above.
    op.execute(
        """
        UPDATE users u
        SET username = s.roll_number
        FROM students s
        WHERE u.id = s.user_id AND u.username LIKE 'std-%'
        """
    )

    op.alter_column('students', 'roll_number', nullable=False)

    # ------------------------------------------------------------------
    # 5. Seed sequence_counters so future generation continues past
    #    whatever was just backfilled. Uses MAX(sequence suffix), not
    #    COUNT(*), so it's correct even if a pre-existing (non-backfilled)
    #    roll_number/employee_code happened to already match the new
    #    format by coincidence.
    # ------------------------------------------------------------------
    op.execute(
        """
        WITH parsed AS (
            SELECT (regexp_match(employee_code, '^EMP-([0-9]{4})$'))[1]::int AS seq
            FROM employees
            WHERE employee_code ~ '^EMP-[0-9]{4}$'
        )
        INSERT INTO sequence_counters (id, scope_key, next_value)
        SELECT gen_random_uuid(), 'employee_id', COALESCE(MAX(seq), 0) + 1
        FROM parsed
        ON CONFLICT (scope_key) DO UPDATE
            SET next_value = GREATEST(sequence_counters.next_value, EXCLUDED.next_value)
        """
    )

    op.execute(
        """
        WITH parsed AS (
            SELECT s.roll_number,
                   COALESCE(p.code, 'GEN') AS prog_code,
                   COALESCE(EXTRACT(YEAR FROM s.enrollment_date), EXTRACT(YEAR FROM s.created_at))::int AS yr,
                   (regexp_match(s.roll_number, '-([0-9]{4})$'))[1]::int AS seq
            FROM students s
            LEFT JOIN programs p ON p.id = s.program_id
            WHERE s.roll_number ~ '^[A-Za-z0-9]+-[0-9]{4}-[0-9]{4}$'
        )
        INSERT INTO sequence_counters (id, scope_key, next_value)
        SELECT gen_random_uuid(), 'student_roll:' || prog_code || ':' || yr, MAX(seq) + 1
        FROM parsed
        GROUP BY prog_code, yr
        ON CONFLICT (scope_key) DO UPDATE
            SET next_value = GREATEST(sequence_counters.next_value, EXCLUDED.next_value)
        """
    )


def downgrade() -> None:
    # Structural reversal only. The username renames and generated
    # values from the backfill are NOT reversible — there is no record
    # of what a username was before this migration renamed it, the same
    # class of one-way data change documented elsewhere in this
    # migration history (e.g. the stray-permission-grant fix). Roll back
    # in a maintenance window with a fresh restore if you truly need the
    # old scheme back, not this downgrade alone.
    op.alter_column('students', 'roll_number', nullable=True)

    op.drop_constraint('uq_employees_employee_code', 'employees', type_='unique')
    op.drop_column('employees', 'employee_code')

    op.drop_table('sequence_counters')