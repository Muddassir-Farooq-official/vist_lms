"""School mode, stage 3: exams, marks, report cards (remarks, skills), publishing and promotion.

Production impact: additive only, safe to run on the live database.
  - seven new, empty tables: school_exams, school_exam_subjects, school_marks, school_skills,
    school_report_notes, school_report_publications and school_promotions, and two enum types
    (school_exam_kind, school_promotion_decision);
  - no existing table or row is changed. They are used only when School mode is on.
Downgrade drops the seven tables and the two enum types (exams, marks, report cards and promotion records made
after the upgrade would be lost; the students' classes and enrolments set by a promotion stay as they are).

Revision ID: a4c6e8a0b2d4
Revises: f3b5d7f9a1c3
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'a4c6e8a0b2d4'
down_revision = 'f3b5d7f9a1c3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('school_skills',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_school_skills')),
    sa.UniqueConstraint('name', name=op.f('uq_school_skills_name'))
    )
    op.create_table('school_exams',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('term_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.Enum('FIRST_TERM', 'MID_TERM', 'FINAL', name='school_exam_kind'), nullable=False),
    sa.Column('marks_open', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_school_exams_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['term_id'], ['academic_terms.id'], name=op.f('fk_school_exams_term_id_academic_terms'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_school_exams')),
    sa.UniqueConstraint('term_id', 'kind', name='uq_school_exams_term_kind')
    )
    op.create_index(op.f('ix_school_exams_term_id'), 'school_exams', ['term_id'], unique=False)
    op.create_table('school_promotions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('from_term_id', sa.Uuid(), nullable=False),
    sa.Column('to_term_id', sa.Uuid(), nullable=False),
    sa.Column('from_program_id', sa.Uuid(), nullable=False),
    sa.Column('to_program_id', sa.Uuid(), nullable=False),
    sa.Column('decision', sa.Enum('PROMOTED', 'PROMOTED_ON_APPROVAL', 'KEPT_BACK', name='school_promotion_decision'), nullable=False),
    sa.Column('percentage', sa.Numeric(precision=5, scale=2), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_school_promotions_decided_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['from_program_id'], ['programs.id'], name=op.f('fk_school_promotions_from_program_id_programs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['from_term_id'], ['academic_terms.id'], name=op.f('fk_school_promotions_from_term_id_academic_terms'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_school_promotions_student_id_students'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['to_program_id'], ['programs.id'], name=op.f('fk_school_promotions_to_program_id_programs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['to_term_id'], ['academic_terms.id'], name=op.f('fk_school_promotions_to_term_id_academic_terms'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_school_promotions')),
    sa.UniqueConstraint('student_id', 'from_term_id', name='uq_school_promotions_student_term')
    )
    op.create_index(op.f('ix_school_promotions_student_id'), 'school_promotions', ['student_id'], unique=False)
    op.create_table('school_exam_subjects',
    sa.Column('exam_id', sa.Uuid(), nullable=False),
    sa.Column('course_id', sa.Uuid(), nullable=False),
    sa.Column('program_id', sa.Uuid(), nullable=False),
    sa.Column('total_marks', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['course_id'], ['courses.id'], name=op.f('fk_school_exam_subjects_course_id_courses'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['exam_id'], ['school_exams.id'], name=op.f('fk_school_exam_subjects_exam_id_school_exams'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['program_id'], ['programs.id'], name=op.f('fk_school_exam_subjects_program_id_programs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('exam_id', 'course_id', name=op.f('pk_school_exam_subjects'))
    )
    op.create_index(op.f('ix_school_exam_subjects_program_id'), 'school_exam_subjects', ['program_id'], unique=False)
    op.create_table('school_marks',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('exam_id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('course_id', sa.Uuid(), nullable=False),
    sa.Column('marks', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('absent', sa.Boolean(), nullable=False),
    sa.Column('entered_by', sa.Uuid(), nullable=True),
    sa.Column('entered_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['course_id'], ['courses.id'], name=op.f('fk_school_marks_course_id_courses'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['entered_by'], ['users.id'], name=op.f('fk_school_marks_entered_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['exam_id'], ['school_exams.id'], name=op.f('fk_school_marks_exam_id_school_exams'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_school_marks_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_school_marks')),
    sa.UniqueConstraint('exam_id', 'student_id', 'course_id', name='uq_school_marks_exam_student_course')
    )
    op.create_index(op.f('ix_school_marks_exam_id'), 'school_marks', ['exam_id'], unique=False)
    op.create_index(op.f('ix_school_marks_student_id'), 'school_marks', ['student_id'], unique=False)
    op.create_table('school_report_notes',
    sa.Column('exam_id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('remarks', sa.String(length=600), nullable=True),
    sa.Column('skills', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('entered_by', sa.Uuid(), nullable=True),
    sa.Column('entered_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['entered_by'], ['users.id'], name=op.f('fk_school_report_notes_entered_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['exam_id'], ['school_exams.id'], name=op.f('fk_school_report_notes_exam_id_school_exams'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_school_report_notes_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('exam_id', 'student_id', name=op.f('pk_school_report_notes'))
    )
    op.create_table('school_report_publications',
    sa.Column('exam_id', sa.Uuid(), nullable=False),
    sa.Column('program_id', sa.Uuid(), nullable=False),
    sa.Column('published_by', sa.Uuid(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['exam_id'], ['school_exams.id'], name=op.f('fk_school_report_publications_exam_id_school_exams'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['program_id'], ['programs.id'], name=op.f('fk_school_report_publications_program_id_programs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['published_by'], ['users.id'], name=op.f('fk_school_report_publications_published_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('exam_id', 'program_id', name=op.f('pk_school_report_publications'))
    )


def downgrade() -> None:
    op.drop_table('school_report_publications')
    op.drop_table('school_report_notes')
    op.drop_index(op.f('ix_school_marks_student_id'), table_name='school_marks')
    op.drop_index(op.f('ix_school_marks_exam_id'), table_name='school_marks')
    op.drop_table('school_marks')
    op.drop_index(op.f('ix_school_exam_subjects_program_id'), table_name='school_exam_subjects')
    op.drop_table('school_exam_subjects')
    op.drop_index(op.f('ix_school_promotions_student_id'), table_name='school_promotions')
    op.drop_table('school_promotions')
    op.drop_index(op.f('ix_school_exams_term_id'), table_name='school_exams')
    op.drop_table('school_exams')
    op.execute("DROP TYPE IF EXISTS school_exam_kind")
    op.execute("DROP TYPE IF EXISTS school_promotion_decision")
    op.drop_table('school_skills')
