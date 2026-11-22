"""clinical rotations: hospitals, wards, groups, supervisors, rotations, clinical attendance, logbook

Production impact: creates nine new, empty tables (clinical_sites, clinical_units, clinical_supervisors,
clinical_groups, clinical_group_members, clinical_rotations, clinical_attendance, clinical_skills,
clinical_log_entries). No existing table or row is changed, so it is quick and takes no lock on busy tables.
The new "Clinical supervisor" role and two permissions come from the permission seed script that the
deploy already runs. Downgrade drops only these nine tables (and any clinical data entered since).

Revision ID: 3abf9c1a9bdb
Revises: 1513e95a01c9
Create Date: 2026-11-22 12:07:57.121409

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3abf9c1a9bdb'
down_revision = '1513e95a01c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('clinical_sites',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('address', sa.String(length=300), nullable=True),
    sa.Column('contact_person', sa.String(length=120), nullable=True),
    sa.Column('contact_phone', sa.String(length=30), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_clinical_sites')),
    sa.UniqueConstraint('name', name=op.f('uq_clinical_sites_name'))
    )
    op.create_table('clinical_units',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('site_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('capacity', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['site_id'], ['clinical_sites.id'], name=op.f('fk_clinical_units_site_id_clinical_sites'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_clinical_units')),
    sa.UniqueConstraint('site_id', 'name', name='uq_clinical_units_site_name')
    )
    op.create_index(op.f('ix_clinical_units_site_id'), 'clinical_units', ['site_id'], unique=False)
    op.create_table('clinical_groups',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('program_id', sa.Uuid(), nullable=True),
    sa.Column('semester', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['program_id'], ['programs.id'], name=op.f('fk_clinical_groups_program_id_programs'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_clinical_groups')),
    sa.UniqueConstraint('name', name=op.f('uq_clinical_groups_name'))
    )
    op.create_table('clinical_skills',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('program_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('required', sa.Integer(), nullable=False),
    sa.Column('semester', sa.Integer(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.ForeignKeyConstraint(['program_id'], ['programs.id'], name=op.f('fk_clinical_skills_program_id_programs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_clinical_skills')),
    sa.UniqueConstraint('program_id', 'name', name='uq_clinical_skills_program_name')
    )
    op.create_index(op.f('ix_clinical_skills_program_id'), 'clinical_skills', ['program_id'], unique=False)
    op.create_table('clinical_supervisors',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('phone', sa.String(length=30), nullable=True),
    sa.Column('site_id', sa.Uuid(), nullable=True),
    sa.Column('employee_id', sa.Uuid(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_clinical_supervisors_employee_id_employees'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['site_id'], ['clinical_sites.id'], name=op.f('fk_clinical_supervisors_site_id_clinical_sites'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_clinical_supervisors_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_clinical_supervisors')),
    sa.UniqueConstraint('user_id', name=op.f('uq_clinical_supervisors_user_id'))
    )
    op.create_table('clinical_group_members',
    sa.Column('group_id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['clinical_groups.id'], name=op.f('fk_clinical_group_members_group_id_clinical_groups'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_clinical_group_members_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('group_id', 'student_id', name=op.f('pk_clinical_group_members'))
    )
    op.create_index(op.f('ix_clinical_group_members_student_id'), 'clinical_group_members', ['student_id'], unique=False)
    op.create_table('clinical_rotations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('group_id', sa.Uuid(), nullable=False),
    sa.Column('unit_id', sa.Uuid(), nullable=False),
    sa.Column('supervisor_id', sa.Uuid(), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('days', sa.String(length=40), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=False),
    sa.Column('end_time', sa.Time(), nullable=False),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_clinical_rotations_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['group_id'], ['clinical_groups.id'], name=op.f('fk_clinical_rotations_group_id_clinical_groups'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['supervisor_id'], ['clinical_supervisors.id'], name=op.f('fk_clinical_rotations_supervisor_id_clinical_supervisors'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['unit_id'], ['clinical_units.id'], name=op.f('fk_clinical_rotations_unit_id_clinical_units'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_clinical_rotations'))
    )
    op.create_index('ix_clinical_rotations_dates', 'clinical_rotations', ['start_date', 'end_date'], unique=False)
    op.create_index(op.f('ix_clinical_rotations_group_id'), 'clinical_rotations', ['group_id'], unique=False)
    op.create_index(op.f('ix_clinical_rotations_supervisor_id'), 'clinical_rotations', ['supervisor_id'], unique=False)
    op.create_index(op.f('ix_clinical_rotations_unit_id'), 'clinical_rotations', ['unit_id'], unique=False)
    op.create_table('clinical_attendance',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('rotation_id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('marked_by', sa.Uuid(), nullable=True),
    sa.Column('marked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['marked_by'], ['users.id'], name=op.f('fk_clinical_attendance_marked_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['rotation_id'], ['clinical_rotations.id'], name=op.f('fk_clinical_attendance_rotation_id_clinical_rotations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_clinical_attendance_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_clinical_attendance')),
    sa.UniqueConstraint('rotation_id', 'student_id', 'day', name='uq_clinical_attendance_rotation_student_day')
    )
    op.create_index(op.f('ix_clinical_attendance_rotation_id'), 'clinical_attendance', ['rotation_id'], unique=False)
    op.create_index(op.f('ix_clinical_attendance_student_id'), 'clinical_attendance', ['student_id'], unique=False)
    op.create_table('clinical_log_entries',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('skill_id', sa.Uuid(), nullable=False),
    sa.Column('rotation_id', sa.Uuid(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('times', sa.Integer(), nullable=False),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reject_note', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_clinical_log_entries_decided_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['rotation_id'], ['clinical_rotations.id'], name=op.f('fk_clinical_log_entries_rotation_id_clinical_rotations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['skill_id'], ['clinical_skills.id'], name=op.f('fk_clinical_log_entries_skill_id_clinical_skills'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_clinical_log_entries_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_clinical_log_entries'))
    )
    op.create_index('ix_clinical_log_entries_rotation_status', 'clinical_log_entries', ['rotation_id', 'status'], unique=False)
    op.create_index('ix_clinical_log_entries_student', 'clinical_log_entries', ['student_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_clinical_log_entries_student', table_name='clinical_log_entries')
    op.drop_index('ix_clinical_log_entries_rotation_status', table_name='clinical_log_entries')
    op.drop_table('clinical_log_entries')
    op.drop_index(op.f('ix_clinical_attendance_student_id'), table_name='clinical_attendance')
    op.drop_index(op.f('ix_clinical_attendance_rotation_id'), table_name='clinical_attendance')
    op.drop_table('clinical_attendance')
    op.drop_index(op.f('ix_clinical_rotations_unit_id'), table_name='clinical_rotations')
    op.drop_index(op.f('ix_clinical_rotations_supervisor_id'), table_name='clinical_rotations')
    op.drop_index(op.f('ix_clinical_rotations_group_id'), table_name='clinical_rotations')
    op.drop_index('ix_clinical_rotations_dates', table_name='clinical_rotations')
    op.drop_table('clinical_rotations')
    op.drop_index(op.f('ix_clinical_group_members_student_id'), table_name='clinical_group_members')
    op.drop_table('clinical_group_members')
    op.drop_table('clinical_supervisors')
    op.drop_index(op.f('ix_clinical_skills_program_id'), table_name='clinical_skills')
    op.drop_table('clinical_skills')
    op.drop_table('clinical_groups')
    op.drop_index(op.f('ix_clinical_units_site_id'), table_name='clinical_units')
    op.drop_table('clinical_units')
    op.drop_table('clinical_sites')
