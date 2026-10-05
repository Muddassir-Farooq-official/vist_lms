"""retroactively rename existing student roll numbers to the new format

Revision ID: e2b7c4f91a6d
Revises: d1f5b8e6c3a4
Create Date: 2026-09-02 00:00:00.000000

REVERSES the "don't retroactively rename" policy stated in every prior
identifier-format migration in this project — per explicit approved
decision, existing students' roll numbers/usernames ARE renamed here to
match the current '<2-digit year><sequence>' format (e.g. '26001').

Renames, per student, in creation order within each enrollment year:
  1. roll_number -> '<2-digit year><seq>' (year from enrollment_date,
     falling back to the row's created_at year if enrollment_date is
     null — same fallback rule app/core/sequences.py itself uses).
  2. The linked user's username -> the new roll_number, unconditionally
     — every student's username has always equalled their roll_number
     by design in this codebase (there has never been a way to
     customize a student's username away from it), so this is a safe
     match, not a guess.
  3. sequence_counters seeded so future admissions continue after
     whatever was just assigned here, same approach already proven in
     migration c7f3a1d95e6b.

WARNING — operational note, not something this migration can do for
you: every existing student's login username changes as a result.
Anyone who already has credentials needs their new roll number/username
communicated to them; the password (vertex123, unless already changed)
is untouched.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'e2b7c4f91a6d'
down_revision = 'd1f5b8e6c3a4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Compute the new roll_number for every student ONCE, sequential
    # per enrollment year, oldest-created first within each year — and
    # reuse this same computation for both the rename (step 1) and the
    # counter seed (step 3), rather than re-deriving the sequence number
    # from the resulting string later (which would be fragile: a
    # string-math approach like 'take the last 3 digits' silently
    # breaks the moment any single year has 1000+ admissions and the
    # sequence grows to 4+ digits).
    op.execute(
        """
        CREATE TEMP TABLE _roll_number_backfill AS
        SELECT s.id,
               COALESCE(EXTRACT(YEAR FROM s.enrollment_date), EXTRACT(YEAR FROM s.created_at))::int AS yr,
               ROW_NUMBER() OVER (
                   PARTITION BY COALESCE(EXTRACT(YEAR FROM s.enrollment_date), EXTRACT(YEAR FROM s.created_at))
                   ORDER BY s.created_at
               ) AS seq
        FROM students s
        """
    )

    # 1. Apply the new roll_number.
    op.execute(
        """
        UPDATE students
        SET roll_number = (b.yr % 100)::text || LPAD(b.seq::text, 3, '0')
        FROM _roll_number_backfill b
        WHERE students.id = b.id
        """
    )

    # 2. Rename the linked user's username to match — every student's
    #    username has always equalled their roll_number by design in
    #    this codebase (see app/students/service.py), so this is a
    #    safe, unconditional match, not a narrow pattern guess. Employee
    #    usernames ('EMP-####') are structurally impossible to hit here
    #    since this join is scoped through the students table only.
    op.execute(
        """
        UPDATE users u
        SET username = s.roll_number
        FROM students s
        WHERE u.id = s.user_id
        """
    )

    # 3. Seed sequence_counters directly from the same computed table,
    #    so future admissions continue correctly — no string parsing.
    op.execute(
        """
        INSERT INTO sequence_counters (id, scope_key, next_value)
        SELECT gen_random_uuid(), 'student_roll:' || yr, MAX(seq) + 1
        FROM _roll_number_backfill
        GROUP BY yr
        ON CONFLICT (scope_key) DO UPDATE
            SET next_value = GREATEST(sequence_counters.next_value, EXCLUDED.next_value)
        """
    )

    op.execute("DROP TABLE _roll_number_backfill")


def downgrade() -> None:
    # Not reversible — there is no record of what a roll_number/username
    # was before this rename, the same one-way-change class as every
    # prior identifier migration in this project. Restore from a backup
    # taken before this migration if you need the old values back.
    pass