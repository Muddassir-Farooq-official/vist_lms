"""attendance and grading module

Revision ID: c9f6a2e83b5d
Revises: b8e5c3a71f4d
Create Date: 2026-09-19 00:00:00.000000

New tables for the attendance and grading modules approved in
conversation: attendance_records (per-lecture, per-student), and
assessment_components / student_marks (dynamic, admin/teacher-defined
weighted grading — replaces the fixed Pass/Fail placeholder as the
normal path, though EnrollmentService.set_outcome still exists for a
genuine manual override).

Also: attendance_locked_at on course_offerings (set once by
AttendanceService.lock_attendance, a deliberate one-way admin action
per offering), and a new ATTENDANCE value on the existing fine_source
enum (attendance-threshold fines auto-issued by that same action).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c9f6a2e83b5d'
down_revision = 'b8e5c3a71f4d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('course_offerings', sa.Column('attendance_locked_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'attendance_records',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('lecture_id', sa.Uuid(), nullable=False),
        sa.Column('student_id', sa.Uuid(), nullable=False),
        sa.Column('status', sa.Enum('PRESENT', 'ABSENT', name='attendance_status'), nullable=False),
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

    op.create_table(
        'assessment_components',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('course_offering_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('max_marks', sa.Numeric(6, 2), nullable=False),
        sa.Column('weight_percentage', sa.Numeric(5, 2), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['course_offering_id'], ['course_offerings.id'], name=op.f('fk_assessment_components_course_offering_id_course_offerings'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_assessment_components_created_by_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_assessment_components')),
    )
    op.create_index('ix_assessment_components_offering_id', 'assessment_components', ['course_offering_id'])

    op.create_table(
        'student_marks',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('enrollment_id', sa.Uuid(), nullable=False),
        sa.Column('assessment_component_id', sa.Uuid(), nullable=False),
        sa.Column('obtained_marks', sa.Numeric(6, 2), nullable=False),
        sa.Column('entered_by', sa.Uuid(), nullable=True),
        sa.Column('entered_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['enrollment_id'], ['enrollments.id'], name=op.f('fk_student_marks_enrollment_id_enrollments'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assessment_component_id'], ['assessment_components.id'], name=op.f('fk_student_marks_assessment_component_id_assessment_components'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['entered_by'], ['users.id'], name=op.f('fk_student_marks_entered_by_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_student_marks')),
        sa.UniqueConstraint('enrollment_id', 'assessment_component_id', name='uq_student_marks_enrollment_component'),
    )
    op.create_index('ix_student_marks_enrollment_id', 'student_marks', ['enrollment_id'])

    op.execute("ALTER TYPE fine_source ADD VALUE IF NOT EXISTS 'ATTENDANCE'")


def downgrade() -> None:
    op.drop_index('ix_student_marks_enrollment_id', table_name='student_marks')
    op.drop_table('student_marks')
    op.drop_index('ix_assessment_components_offering_id', table_name='assessment_components')
    op.drop_table('assessment_components')
    op.drop_index('ix_attendance_records_lecture_id', table_name='attendance_records')
    op.drop_index('ix_attendance_records_student_id', table_name='attendance_records')
    op.drop_table('attendance_records')
    op.execute("DROP TYPE IF EXISTS attendance_status")
    op.drop_column('course_offerings', 'attendance_locked_at')
    # Not removing the ATTENDANCE fine_source value — same one-way-change
    # reasoning as every other enum addition in this project.