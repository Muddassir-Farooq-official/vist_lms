"""academics module — courses, offerings, enrollment, lectures + program credit-hour cap

Revision ID: a1c9e7f43d2b
Revises: f3a8d6c25b7e
Create Date: 2026-09-15 00:00:00.000000

New curriculum domain, built per the approved plan:
- Program gains max_credit_hours_per_semester (per-program, not a
  global constant — approved decision) — backfilled to 18 for all
  existing programs, a sane default admins can change per program.
- Course: the abstract subject, shareable across programs (approved
  decision) — its per-program semester slot lives in CourseProgram,
  not on Course itself.
- CourseOffering: one term's actual instance of a course, what a
  teacher is assigned to and what students enroll in.
- Enrollment: a student's registration in one offering — a retake is
  just a second Enrollment row against a later offering of the same
  underlying course, with is_retake=True.
- Lecture / LectureResource: per-offering lecture list and their
  uploaded PDF resources (notes/slides/other).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1c9e7f43d2b'
down_revision = 'f3a8d6c25b7e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Program: credit-hour cap ---
    op.add_column('programs', sa.Column('max_credit_hours_per_semester', sa.Integer(), server_default='18', nullable=False))
    op.alter_column('programs', 'max_credit_hours_per_semester', server_default=None)

    # --- Course ---
    op.create_table(
        'courses',
        sa.Column('code', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('credit_hours', sa.Integer(), nullable=False),
        sa.Column('description', sa.String(length=1000), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_courses')),
        sa.UniqueConstraint('code', name='uq_courses_code'),
    )
    op.alter_column('courses', 'is_active', server_default=None)

    # --- CourseProgram (join table, no separate id/timestamps) ---
    op.create_table(
        'course_programs',
        sa.Column('course_id', sa.Uuid(), nullable=False),
        sa.Column('program_id', sa.Uuid(), nullable=False),
        sa.Column('semester_number', sa.Integer(), nullable=False),
        sa.CheckConstraint('semester_number > 0', name='positive_semester_number'),
        sa.ForeignKeyConstraint(['course_id'], ['courses.id'], name=op.f('fk_course_programs_course_id_courses'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['program_id'], ['programs.id'], name=op.f('fk_course_programs_program_id_programs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('course_id', 'program_id', name=op.f('pk_course_programs')),
    )

    # --- CourseOffering ---
    op.create_table(
        'course_offerings',
        sa.Column('course_id', sa.Uuid(), nullable=False),
        sa.Column('term_label', sa.String(length=50), nullable=False),
        sa.Column('assigned_teacher_id', sa.Uuid(), nullable=True),
        sa.Column('is_open_for_enrollment', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('syllabus_storage_key', sa.String(length=255), nullable=True),
        sa.Column('syllabus_original_name', sa.String(length=255), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['course_id'], ['courses.id'], name=op.f('fk_course_offerings_course_id_courses'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assigned_teacher_id'], ['employees.id'], name=op.f('fk_course_offerings_assigned_teacher_id_employees'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_course_offerings')),
        sa.UniqueConstraint('course_id', 'term_label', name='uq_course_offerings_course_term'),
    )
    op.alter_column('course_offerings', 'is_open_for_enrollment', server_default=None)

    # --- Enrollment ---
    op.create_table(
        'enrollments',
        sa.Column('student_id', sa.Uuid(), nullable=False),
        sa.Column('course_offering_id', sa.Uuid(), nullable=False),
        sa.Column('status', sa.Enum('ENROLLED', 'COMPLETED', 'WITHDRAWN', name='enrollment_status'), nullable=False),
        sa.Column('outcome', sa.Enum('PASSED', 'FAILED', name='enrollment_outcome'), nullable=True),
        sa.Column('is_retake', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('enrolled_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_enrollments_student_id_students'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['course_offering_id'], ['course_offerings.id'], name=op.f('fk_enrollments_course_offering_id_course_offerings'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_enrollments')),
        sa.UniqueConstraint('student_id', 'course_offering_id', name='uq_enrollments_student_offering'),
    )
    op.alter_column('enrollments', 'is_retake', server_default=None)
    op.create_index('ix_enrollments_student_id', 'enrollments', ['student_id'], unique=False)
    op.create_index('ix_enrollments_course_offering_id', 'enrollments', ['course_offering_id'], unique=False)

    # --- Lecture ---
    op.create_table(
        'lectures',
        sa.Column('course_offering_id', sa.Uuid(), nullable=False),
        sa.Column('lecture_number', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.String(length=1000), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['course_offering_id'], ['course_offerings.id'], name=op.f('fk_lectures_course_offering_id_course_offerings'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_lectures')),
        sa.UniqueConstraint('course_offering_id', 'lecture_number', name='uq_lectures_offering_number'),
    )

    # --- LectureResource ---
    op.create_table(
        'lecture_resources',
        sa.Column('lecture_id', sa.Uuid(), nullable=False),
        sa.Column('resource_type', sa.Enum('NOTES', 'SLIDES', 'OTHER', name='lecture_resource_type'), nullable=False),
        sa.Column('storage_key', sa.String(length=255), nullable=False),
        sa.Column('original_name', sa.String(length=255), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['lecture_id'], ['lectures.id'], name=op.f('fk_lecture_resources_lecture_id_lectures'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_lecture_resources')),
    )
    op.create_index('ix_lecture_resources_lecture_id', 'lecture_resources', ['lecture_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_lecture_resources_lecture_id', table_name='lecture_resources')
    op.drop_table('lecture_resources')
    op.execute("DROP TYPE IF EXISTS lecture_resource_type")

    op.drop_table('lectures')

    op.drop_index('ix_enrollments_course_offering_id', table_name='enrollments')
    op.drop_index('ix_enrollments_student_id', table_name='enrollments')
    op.drop_table('enrollments')
    op.execute("DROP TYPE IF EXISTS enrollment_outcome")
    op.execute("DROP TYPE IF EXISTS enrollment_status")

    op.drop_table('course_offerings')
    op.drop_table('course_programs')
    op.drop_table('courses')

    op.drop_column('programs', 'max_credit_hours_per_semester')