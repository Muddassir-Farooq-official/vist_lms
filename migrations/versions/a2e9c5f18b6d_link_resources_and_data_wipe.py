"""class session resources support links; wipe timetable/attendance/grading data

Revision ID: a2e9c5f18b6d
Revises: f7c4d1a836e9
Create Date: 2026-09-26 00:00:00.000000

Two things, per explicit request in conversation:

1. ClassSessionResource can now be either an uploaded file OR a link
   (e.g. a Slides URL) -- storage_key becomes nullable, a new
   external_url column is added, and a check constraint enforces
   exactly one of the two is ever set.

2. A full wipe of everything built in the last several rounds of
   iteration -- periods, timetable_slots, class_sessions,
   class_session_resources, attendance_records, student_marks, and
   assessment_components -- so the admin panel starts genuinely empty
   rather than carrying forward test data from development. Courses,
   programs, offerings, enrollments, students, and employees are
   completely untouched; only the schedule/attendance/grading data
   this session's features generated gets cleared. This is a one-way,
   deliberately irreversible data change -- the downgrade only reverts
   the schema, it does not attempt to restore deleted rows.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a2e9c5f18b6d'
down_revision = 'f7c4d1a836e9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Delete in FK-dependency order: children before parents. Only
    # this feature's own tables -- courses/programs/offerings/
    # enrollments/students/employees are never touched.
    op.execute("DELETE FROM attendance_records")
    op.execute("DELETE FROM student_marks")
    op.execute("DELETE FROM assessment_components")
    op.execute("DELETE FROM class_session_resources")
    op.execute("DELETE FROM class_sessions")
    op.execute("DELETE FROM timetable_slots")
    op.execute("DELETE FROM periods")

    op.alter_column('class_session_resources', 'storage_key', existing_type=sa.String(length=255), nullable=True)
    op.add_column('class_session_resources', sa.Column('external_url', sa.String(length=2048), nullable=True))
    op.create_check_constraint(
        'ck_class_session_resources_exactly_one_of_file_or_link', 'class_session_resources',
        '(storage_key IS NOT NULL) != (external_url IS NOT NULL)',
    )


def downgrade() -> None:
    op.drop_constraint('ck_class_session_resources_exactly_one_of_file_or_link', 'class_session_resources', type_='check')
    op.drop_column('class_session_resources', 'external_url')
    op.alter_column('class_session_resources', 'storage_key', existing_type=sa.String(length=255), nullable=False)
    # The data deletion above is deliberately not reversed -- there is
    # nothing to restore it from.