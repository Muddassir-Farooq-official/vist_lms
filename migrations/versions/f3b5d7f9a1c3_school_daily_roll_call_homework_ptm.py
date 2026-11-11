"""School mode, stage 2: the morning roll call, the homework diary and parent-teacher meetings.

Production impact: additive only, safe to run on the live database.
  - five new, empty tables: school_daily_attendance, school_roll_call_cover, school_homework,
    school_ptm_days and school_ptm_slots (and one new enum type, school_day_status);
  - no existing table or row is changed. They are used only when School mode is on.
Downgrade drops the five tables and the enum type (roll calls, homework and meeting bookings made after
the upgrade would be lost; homework files stay on disk).

Revision ID: f3b5d7f9a1c3
Revises: e2a4c6e8f0b2
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3b5d7f9a1c3'
down_revision = 'e2a4c6e8f0b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('school_ptm_days',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('term_id', sa.Uuid(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=False),
    sa.Column('end_time', sa.Time(), nullable=False),
    sa.Column('slot_minutes', sa.Integer(), nullable=False),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('cancelled', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_school_ptm_days_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['term_id'], ['academic_terms.id'], name=op.f('fk_school_ptm_days_term_id_academic_terms'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_school_ptm_days'))
    )
    op.create_index(op.f('ix_school_ptm_days_term_id'), 'school_ptm_days', ['term_id'], unique=False)
    op.create_table('school_daily_attendance',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('section_id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('status', sa.Enum('PRESENT', 'ABSENT', 'LATE', 'LEAVE', name='school_day_status'), nullable=False),
    sa.Column('note', sa.String(length=200), nullable=True),
    sa.Column('marked_by', sa.Uuid(), nullable=True),
    sa.Column('marked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('told_status', sa.String(length=10), nullable=True),
    sa.ForeignKeyConstraint(['marked_by'], ['users.id'], name=op.f('fk_school_daily_attendance_marked_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['section_id'], ['school_sections.id'], name=op.f('fk_school_daily_attendance_section_id_school_sections'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_school_daily_attendance_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_school_daily_attendance')),
    sa.UniqueConstraint('student_id', 'day', name='uq_school_daily_attendance_student_day')
    )
    op.create_index('ix_school_daily_attendance_section_day', 'school_daily_attendance', ['section_id', 'day'], unique=False)
    op.create_table('school_homework',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('section_id', sa.Uuid(), nullable=False),
    sa.Column('course_id', sa.Uuid(), nullable=False),
    sa.Column('set_on', sa.Date(), nullable=False),
    sa.Column('due_on', sa.Date(), nullable=True),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('details', sa.String(length=2000), nullable=True),
    sa.Column('file_key', sa.String(length=300), nullable=True),
    sa.Column('file_name', sa.String(length=255), nullable=True),
    sa.Column('file_type', sa.String(length=100), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['course_id'], ['courses.id'], name=op.f('fk_school_homework_course_id_courses'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_school_homework_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['section_id'], ['school_sections.id'], name=op.f('fk_school_homework_section_id_school_sections'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_school_homework'))
    )
    op.create_index('ix_school_homework_section_day', 'school_homework', ['section_id', 'set_on'], unique=False)
    op.create_table('school_ptm_slots',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('ptm_day_id', sa.Uuid(), nullable=False),
    sa.Column('employee_id', sa.Uuid(), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=False),
    sa.Column('end_time', sa.Time(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=True),
    sa.Column('booked_by', sa.Uuid(), nullable=True),
    sa.Column('booked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notes', sa.String(length=1000), nullable=True),
    sa.Column('attended', sa.Boolean(), nullable=True),
    sa.ForeignKeyConstraint(['booked_by'], ['users.id'], name=op.f('fk_school_ptm_slots_booked_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_school_ptm_slots_employee_id_employees'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ptm_day_id'], ['school_ptm_days.id'], name=op.f('fk_school_ptm_slots_ptm_day_id_school_ptm_days'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_school_ptm_slots_student_id_students'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_school_ptm_slots')),
    sa.UniqueConstraint('ptm_day_id', 'employee_id', 'start_time', name='uq_school_ptm_slots_day_teacher_time'),
    sa.UniqueConstraint('ptm_day_id', 'student_id', name='uq_school_ptm_slots_day_student')
    )
    op.create_index(op.f('ix_school_ptm_slots_employee_id'), 'school_ptm_slots', ['employee_id'], unique=False)
    op.create_index(op.f('ix_school_ptm_slots_ptm_day_id'), 'school_ptm_slots', ['ptm_day_id'], unique=False)
    op.create_table('school_roll_call_cover',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('section_id', sa.Uuid(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('employee_id', sa.Uuid(), nullable=False),
    sa.Column('set_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_school_roll_call_cover_employee_id_employees'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['section_id'], ['school_sections.id'], name=op.f('fk_school_roll_call_cover_section_id_school_sections'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['set_by'], ['users.id'], name=op.f('fk_school_roll_call_cover_set_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_school_roll_call_cover')),
    sa.UniqueConstraint('section_id', 'day', name='uq_school_roll_call_cover_section_day')
    )
    op.create_index(op.f('ix_school_roll_call_cover_employee_id'), 'school_roll_call_cover', ['employee_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_school_roll_call_cover_employee_id'), table_name='school_roll_call_cover')
    op.drop_table('school_roll_call_cover')
    op.drop_index(op.f('ix_school_ptm_slots_ptm_day_id'), table_name='school_ptm_slots')
    op.drop_index(op.f('ix_school_ptm_slots_employee_id'), table_name='school_ptm_slots')
    op.drop_table('school_ptm_slots')
    op.drop_index('ix_school_homework_section_day', table_name='school_homework')
    op.drop_table('school_homework')
    op.drop_index('ix_school_daily_attendance_section_day', table_name='school_daily_attendance')
    op.drop_table('school_daily_attendance')
    op.execute("DROP TYPE IF EXISTS school_day_status")
    op.drop_index(op.f('ix_school_ptm_days_term_id'), table_name='school_ptm_days')
    op.drop_table('school_ptm_days')
