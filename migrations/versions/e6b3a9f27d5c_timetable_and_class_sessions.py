"""timetable and class sessions replace lectures

Revision ID: e6b3a9f27d5c
Revises: d1a7e4c95f2b
Create Date: 2026-09-24 00:00:00.000000

Approved decision: a full replacement of the Lecture/LectureResource
model with a recurring TimetableSlot (weekly pattern, room/teacher/
student-batch conflict-checked at creation) that generates ClassSession
rows (the actual dated occurrences) for every matching weekday between
a course offering's new term_start_date/term_end_date. ClassSession
absorbs both roles the old Lecture played: attendance now attaches to
it (attendance_records.lecture_id -> class_session_id), and its
title/description/uploaded notes-slides are the per-session content,
filled in as the term progresses rather than all pre-created up front.

No production data exists for lectures/lecture_resources/
attendance_records yet (Phase A build) so this drops and recreates
those tables rather than attempting a row-by-row migration -- there is
nothing to preserve.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'e6b3a9f27d5c'
down_revision = 'd1a7e4c95f2b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('course_offerings', sa.Column('term_start_date', sa.Date(), nullable=True))
    op.add_column('course_offerings', sa.Column('term_end_date', sa.Date(), nullable=True))

    # Drop in FK-dependency order: attendance_records -> lecture_resources -> lectures.
    op.drop_table('attendance_records')
    op.execute("DROP TYPE IF EXISTS attendance_status")
    op.drop_table('lecture_resources')
    op.drop_table('lectures')
    # lecture_resource_type is now orphaned (no column references it) --
    # drop and recreate fresh for class_session_resources below, rather
    # than fighting SQLAlchemy/Alembic's create_type=False handling
    # inside op.create_table for a type that already exists.
    op.execute("DROP TYPE IF EXISTS lecture_resource_type")

    op.create_table(
        'timetable_slots',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('course_offering_id', sa.Uuid(), nullable=False),
        sa.Column('day_of_week', sa.Enum(
            'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY', 'SUNDAY', name='day_of_week',
        ), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.Column('room', sa.String(length=50), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['course_offering_id'], ['course_offerings.id'], name=op.f('fk_timetable_slots_course_offering_id_course_offerings'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_timetable_slots_created_by_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_timetable_slots')),
    )

    op.create_table(
        'class_sessions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('course_offering_id', sa.Uuid(), nullable=False),
        sa.Column('timetable_slot_id', sa.Uuid(), nullable=True),
        sa.Column('session_date', sa.Date(), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.Column('room', sa.String(length=50), nullable=False),
        sa.Column('status', sa.Enum('SCHEDULED', 'CANCELLED', name='class_session_status'), nullable=False),
        sa.Column('cancelled_reason', sa.String(length=255), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('description', sa.String(length=1000), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['course_offering_id'], ['course_offerings.id'], name=op.f('fk_class_sessions_course_offering_id_course_offerings'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['timetable_slot_id'], ['timetable_slots.id'], name=op.f('fk_class_sessions_timetable_slot_id_timetable_slots'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_class_sessions')),
        sa.UniqueConstraint('course_offering_id', 'session_date', 'start_time', name='uq_class_sessions_offering_date_start'),
    )
    op.create_index('ix_class_sessions_offering_id', 'class_sessions', ['course_offering_id'])
    op.create_index('ix_class_sessions_slot_id', 'class_sessions', ['timetable_slot_id'])

    op.execute("CREATE TYPE lecture_resource_type AS ENUM ('NOTES', 'SLIDES', 'OTHER')")
    op.create_table(
        'class_session_resources',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('class_session_id', sa.Uuid(), nullable=False),
        sa.Column('resource_type', postgresql.ENUM('NOTES', 'SLIDES', 'OTHER', name='lecture_resource_type', create_type=False), nullable=False),
        sa.Column('storage_key', sa.String(length=255), nullable=False),
        sa.Column('original_name', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['class_session_id'], ['class_sessions.id'], name=op.f('fk_class_session_resources_class_session_id_class_sessions'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_class_session_resources')),
    )
    op.create_index('ix_class_session_resources_session_id', 'class_session_resources', ['class_session_id'])

    op.execute("CREATE TYPE attendance_status AS ENUM ('PRESENT', 'ABSENT')")
    op.create_table(
        'attendance_records',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('class_session_id', sa.Uuid(), nullable=False),
        sa.Column('student_id', sa.Uuid(), nullable=False),
        sa.Column('status', postgresql.ENUM('PRESENT', 'ABSENT', name='attendance_status', create_type=False), nullable=False),
        sa.Column('marked_by', sa.Uuid(), nullable=True),
        sa.Column('marked_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['class_session_id'], ['class_sessions.id'], name=op.f('fk_attendance_records_class_session_id_class_sessions'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_attendance_records_student_id_students'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['marked_by'], ['users.id'], name=op.f('fk_attendance_records_marked_by_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_attendance_records')),
        sa.UniqueConstraint('class_session_id', 'student_id', name='uq_attendance_records_session_student'),
    )
    op.create_index('ix_attendance_records_student_id', 'attendance_records', ['student_id'])
    op.create_index('ix_attendance_records_class_session_id', 'attendance_records', ['class_session_id'])


def downgrade() -> None:
    op.drop_table('attendance_records')
    op.execute("DROP TYPE IF EXISTS attendance_status")

    op.drop_index('ix_class_session_resources_session_id', table_name='class_session_resources')
    op.drop_table('class_session_resources')
    op.execute("DROP TYPE IF EXISTS lecture_resource_type")

    op.drop_index('ix_class_sessions_slot_id', table_name='class_sessions')
    op.drop_index('ix_class_sessions_offering_id', table_name='class_sessions')
    op.drop_table('class_sessions')
    op.execute("DROP TYPE IF EXISTS class_session_status")

    op.drop_table('timetable_slots')
    op.execute("DROP TYPE IF EXISTS day_of_week")

    # Recreate the old lectures/lecture_resources/attendance_records
    # shape so a downgrade leaves the schema consistent -- empty tables,
    # since (as noted above) there was never any real data to restore.
    op.create_table(
        'lectures',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('course_offering_id', sa.Uuid(), nullable=False),
        sa.Column('lecture_number', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.String(length=1000), nullable=True),
        sa.Column('room', sa.String(length=50), nullable=True),
        sa.Column('scheduled_start_time', sa.Time(), nullable=True),
        sa.Column('scheduled_end_time', sa.Time(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['course_offering_id'], ['course_offerings.id'], name=op.f('fk_lectures_course_offering_id_course_offerings'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_lectures')),
        sa.UniqueConstraint('course_offering_id', 'lecture_number', name='uq_lectures_offering_number'),
    )
    op.execute("CREATE TYPE lecture_resource_type AS ENUM ('NOTES', 'SLIDES', 'OTHER')")
    op.create_table(
        'lecture_resources',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('lecture_id', sa.Uuid(), nullable=False),
        sa.Column('resource_type', postgresql.ENUM('NOTES', 'SLIDES', 'OTHER', name='lecture_resource_type', create_type=False), nullable=False),
        sa.Column('storage_key', sa.String(length=255), nullable=False),
        sa.Column('original_name', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['lecture_id'], ['lectures.id'], name=op.f('fk_lecture_resources_lecture_id_lectures'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_lecture_resources')),
    )
    op.create_index('ix_lecture_resources_lecture_id', 'lecture_resources', ['lecture_id'])
    op.execute("CREATE TYPE attendance_status AS ENUM ('PRESENT', 'ABSENT')")
    op.create_table(
        'attendance_records',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('lecture_id', sa.Uuid(), nullable=False),
        sa.Column('student_id', sa.Uuid(), nullable=False),
        sa.Column('status', postgresql.ENUM('PRESENT', 'ABSENT', name='attendance_status', create_type=False), nullable=False),
        sa.Column('marked_by', sa.Uuid(), nullable=True),
        sa.Column('marked_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['lecture_id'], ['lectures.id'], name=op.f('fk_attendance_records_lecture_id_lectures'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_attendance_records_student_id_students'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['marked_by'], ['users.id'], name=op.f('fk_attendance_records_marked_by_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_attendance_records')),
        sa.UniqueConstraint('lecture_id', 'student_id', name='uq_attendance_records_lecture_student'),
    )
    op.create_index('ix_attendance_records_student_id', 'attendance_records', ['student_id'])
    op.create_index('ix_attendance_records_lecture_id', 'attendance_records', ['lecture_id'])

    op.drop_column('course_offerings', 'term_end_date')
    op.drop_column('course_offerings', 'term_start_date')