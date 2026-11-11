"""School mode: classes, sections and subjects of a class.

Production impact: additive only, safe to run on the live database.
  - two new, empty tables: class_subjects and school_sections;
  - four new nullable columns on programs (school_level, school_stage, stream, promotes_to_id) with no default,
    so existing rows are not rewritten and every existing program stays a college / university program;
  - School mode stays OFF (setting school_mode = 0) until an administrator switches it on, so nothing
    changes for users of an existing copy.
Nothing existing is changed or deleted.
Downgrade drops the two tables and four columns (classes, sections and subjects set up in School mode
would lose their school details; the programs, courses and offerings themselves remain).

Revision ID: d1f3b5c7e9a1
Revises: c0e2a4b6d8f0
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd1f3b5c7e9a1'
down_revision = 'c0e2a4b6d8f0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('class_subjects',
    sa.Column('program_id', sa.Uuid(), nullable=False),
    sa.Column('course_id', sa.Uuid(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('is_optional', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['course_id'], ['courses.id'], name=op.f('fk_class_subjects_course_id_courses'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['program_id'], ['programs.id'], name=op.f('fk_class_subjects_program_id_programs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('program_id', 'course_id', name=op.f('pk_class_subjects'))
    )
    op.create_table('school_sections',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('term_id', sa.Uuid(), nullable=False),
    sa.Column('program_id', sa.Uuid(), nullable=False),
    sa.Column('label', sa.String(length=30), nullable=False),
    sa.Column('class_teacher_id', sa.Uuid(), nullable=True),
    sa.Column('room', sa.String(length=50), nullable=True),
    sa.Column('capacity', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['class_teacher_id'], ['employees.id'], name=op.f('fk_school_sections_class_teacher_id_employees'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['program_id'], ['programs.id'], name=op.f('fk_school_sections_program_id_programs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['term_id'], ['academic_terms.id'], name=op.f('fk_school_sections_term_id_academic_terms'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_school_sections')),
    sa.UniqueConstraint('term_id', 'program_id', 'label', name='uq_school_sections_term_program_label')
    )
    op.create_index(op.f('ix_school_sections_class_teacher_id'), 'school_sections', ['class_teacher_id'], unique=False)
    op.create_index(op.f('ix_school_sections_program_id'), 'school_sections', ['program_id'], unique=False)
    op.create_index(op.f('ix_school_sections_term_id'), 'school_sections', ['term_id'], unique=False)
    op.add_column('programs', sa.Column('school_level', sa.Integer(), nullable=True))
    op.add_column('programs', sa.Column('school_stage', sa.String(length=20), nullable=True))
    op.add_column('programs', sa.Column('stream', sa.String(length=40), nullable=True))
    op.add_column('programs', sa.Column('promotes_to_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f('fk_programs_promotes_to_id_programs'), 'programs', 'programs', ['promotes_to_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_programs_promotes_to_id_programs'), 'programs', type_='foreignkey')
    op.drop_column('programs', 'promotes_to_id')
    op.drop_column('programs', 'stream')
    op.drop_column('programs', 'school_stage')
    op.drop_column('programs', 'school_level')
    op.drop_index(op.f('ix_school_sections_term_id'), table_name='school_sections')
    op.drop_index(op.f('ix_school_sections_program_id'), table_name='school_sections')
    op.drop_index(op.f('ix_school_sections_class_teacher_id'), table_name='school_sections')
    op.drop_table('school_sections')
    op.drop_table('class_subjects')
