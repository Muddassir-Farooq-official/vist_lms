"""Outcome-based education: PLOs, CLOs, their mapping, course files, assessment items and question-part marks.

Production impact: seven new empty tables (obe_plos, obe_program_settings, obe_clos, obe_clo_plos, obe_course_files,
obe_items, obe_item_marks). No existing table or row changes; marks, results and transcripts are untouched.

Revision ID: 8f4986730b29
Revises: 3e2e573a6780
Create Date: 2026-11-22 10:09:30.727984

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8f4986730b29'
down_revision = '3e2e573a6780'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('obe_plos',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('program_id', sa.Uuid(), nullable=False),
    sa.Column('code', sa.String(length=12), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['program_id'], ['programs.id'], name=op.f('fk_obe_plos_program_id_programs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_obe_plos')),
    sa.UniqueConstraint('program_id', 'code', name='uq_obe_plos_program_code')
    )
    op.create_index(op.f('ix_obe_plos_program_id'), 'obe_plos', ['program_id'], unique=False)
    op.create_table('obe_program_settings',
    sa.Column('program_id', sa.Uuid(), nullable=False),
    sa.Column('student_pct', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('batch_pct', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.ForeignKeyConstraint(['program_id'], ['programs.id'], name=op.f('fk_obe_program_settings_program_id_programs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('program_id', name=op.f('pk_obe_program_settings'))
    )
    op.create_table('obe_clos',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('course_offering_id', sa.Uuid(), nullable=False),
    sa.Column('code', sa.String(length=12), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('bloom', sa.String(length=4), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['course_offering_id'], ['course_offerings.id'], name=op.f('fk_obe_clos_course_offering_id_course_offerings'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_obe_clos')),
    sa.UniqueConstraint('course_offering_id', 'code', name='uq_obe_clos_offering_code')
    )
    op.create_index(op.f('ix_obe_clos_course_offering_id'), 'obe_clos', ['course_offering_id'], unique=False)
    op.create_table('obe_course_files',
    sa.Column('course_offering_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('submitted_by', sa.Uuid(), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.ForeignKeyConstraint(['course_offering_id'], ['course_offerings.id'], name=op.f('fk_obe_course_files_course_offering_id_course_offerings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_obe_course_files_decided_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['submitted_by'], ['users.id'], name=op.f('fk_obe_course_files_submitted_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('course_offering_id', name=op.f('pk_obe_course_files'))
    )
    op.create_table('obe_clo_plos',
    sa.Column('clo_id', sa.Uuid(), nullable=False),
    sa.Column('plo_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['clo_id'], ['obe_clos.id'], name=op.f('fk_obe_clo_plos_clo_id_obe_clos'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['plo_id'], ['obe_plos.id'], name=op.f('fk_obe_clo_plos_plo_id_obe_plos'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('clo_id', 'plo_id', name=op.f('pk_obe_clo_plos'))
    )
    op.create_table('obe_items',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('offering_assessment_id', sa.Uuid(), nullable=False),
    sa.Column('clo_id', sa.Uuid(), nullable=False),
    sa.Column('label', sa.String(length=20), nullable=True),
    sa.Column('max_marks', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['clo_id'], ['obe_clos.id'], name=op.f('fk_obe_items_clo_id_obe_clos'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['offering_assessment_id'], ['offering_assessments.id'], name=op.f('fk_obe_items_offering_assessment_id_offering_assessments'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_obe_items'))
    )
    op.create_index('ix_obe_items_assessment', 'obe_items', ['offering_assessment_id'], unique=False)
    op.create_index(op.f('ix_obe_items_clo_id'), 'obe_items', ['clo_id'], unique=False)
    op.create_table('obe_item_marks',
    sa.Column('item_id', sa.Uuid(), nullable=False),
    sa.Column('enrollment_id', sa.Uuid(), nullable=False),
    sa.Column('obtained', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('entered_by', sa.Uuid(), nullable=True),
    sa.Column('entered_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['enrollment_id'], ['enrollments.id'], name=op.f('fk_obe_item_marks_enrollment_id_enrollments'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['entered_by'], ['users.id'], name=op.f('fk_obe_item_marks_entered_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['item_id'], ['obe_items.id'], name=op.f('fk_obe_item_marks_item_id_obe_items'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('item_id', 'enrollment_id', name=op.f('pk_obe_item_marks'))
    )


def downgrade() -> None:
    op.drop_table('obe_item_marks')
    op.drop_index(op.f('ix_obe_items_clo_id'), table_name='obe_items')
    op.drop_index('ix_obe_items_assessment', table_name='obe_items')
    op.drop_table('obe_items')
    op.drop_table('obe_clo_plos')
    op.drop_table('obe_course_files')
    op.drop_index(op.f('ix_obe_clos_course_offering_id'), table_name='obe_clos')
    op.drop_table('obe_clos')
    op.drop_table('obe_program_settings')
    op.drop_index(op.f('ix_obe_plos_program_id'), table_name='obe_plos')
    op.drop_table('obe_plos')
