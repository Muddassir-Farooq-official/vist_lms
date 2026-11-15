"""Grading tables per affiliating body, mark splits, and the extra parts of a frozen result.

Production impact (all additive; nothing existing is changed or re-graded):
- new empty tables grading_schemes, grading_bands, mark_splits;
- programs.grading_scheme_id (NULL = keep using the institute's default scale, exactly as today);
- assessment_types.component (NULL = Sessional; only matters once a mark split is set);
- enrollment_results: theory_percent, practical_percent, failed_part, scheme_name, division (NULL for every result
  published so far; published results are never recomputed).
Until an administrator assigns a table to a program or sets a mark split, every result is computed exactly as before.
Downgrade drops the new tables and columns (tables, splits and the extra result details typed in would be lost).

Revision ID: f9b1d3e5a7c9
Revises: e8a0c2d4f6b8
"""
from alembic import op
import sqlalchemy as sa


revision = 'f9b1d3e5a7c9'
down_revision = 'e8a0c2d4f6b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'grading_schemes',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('body', sa.String(length=150), nullable=True),
        sa.Column('system', sa.String(length=10), nullable=False),
        sa.Column('pass_percent', sa.Numeric(5, 2), nullable=False),
        sa.Column('theory_pass_percent', sa.Numeric(5, 2), nullable=True),
        sa.Column('practical_pass_percent', sa.Numeric(5, 2), nullable=True),
        sa.Column('resit_cap_percent', sa.Numeric(5, 2), nullable=True),
        sa.Column('min_gpa_promotion', sa.Numeric(3, 2), nullable=True),
        sa.Column('probation_gpa', sa.Numeric(3, 2), nullable=True),
        sa.Column('min_cgpa_degree', sa.Numeric(3, 2), nullable=True),
        sa.Column('sliding_points', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('is_draft', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_table(
        'grading_bands',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('scheme_id', sa.Uuid(), nullable=False),
        sa.Column('letter', sa.String(length=4), nullable=False),
        sa.Column('min_percent', sa.Numeric(5, 2), nullable=False),
        sa.Column('grade_points', sa.Numeric(3, 2), nullable=False),
        sa.Column('grade_points_low', sa.Numeric(3, 2), nullable=True),
        sa.Column('is_passing', sa.Boolean(), nullable=False),
        sa.Column('division', sa.String(length=40), nullable=True),
        sa.Column('remark', sa.String(length=60), nullable=True),
        sa.ForeignKeyConstraint(['scheme_id'], ['grading_schemes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('scheme_id', 'letter', name='uq_grading_band_letter'),
        sa.UniqueConstraint('scheme_id', 'min_percent', name='uq_grading_band_min'),
    )
    op.create_index('ix_grading_bands_scheme_id', 'grading_bands', ['scheme_id'])
    op.create_table(
        'mark_splits',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('program_id', sa.Uuid(), nullable=True),
        sa.Column('course_id', sa.Uuid(), nullable=True),
        sa.Column('sessional', sa.Numeric(6, 2), nullable=False),
        sa.Column('mid', sa.Numeric(6, 2), nullable=False),
        sa.Column('final', sa.Numeric(6, 2), nullable=False),
        sa.Column('practical', sa.Numeric(6, 2), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['program_id'], ['programs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['course_id'], ['courses.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('program_id'),
        sa.UniqueConstraint('course_id'),
    )
    op.add_column('programs', sa.Column('grading_scheme_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('fk_programs_grading_scheme', 'programs', 'grading_schemes', ['grading_scheme_id'], ['id'], ondelete='SET NULL')
    op.add_column('assessment_types', sa.Column('component', sa.String(length=10), nullable=True))
    op.add_column('enrollment_results', sa.Column('theory_percent', sa.Numeric(5, 2), nullable=True))
    op.add_column('enrollment_results', sa.Column('practical_percent', sa.Numeric(5, 2), nullable=True))
    op.add_column('enrollment_results', sa.Column('failed_part', sa.String(length=20), nullable=True))
    op.add_column('enrollment_results', sa.Column('scheme_name', sa.String(length=100), nullable=True))
    op.add_column('enrollment_results', sa.Column('division', sa.String(length=40), nullable=True))


def downgrade() -> None:
    for col in ('division', 'scheme_name', 'failed_part', 'practical_percent', 'theory_percent'):
        op.drop_column('enrollment_results', col)
    op.drop_column('assessment_types', 'component')
    op.drop_constraint('fk_programs_grading_scheme', 'programs', type_='foreignkey')
    op.drop_column('programs', 'grading_scheme_id')
    op.drop_table('mark_splits')
    op.drop_index('ix_grading_bands_scheme_id', table_name='grading_bands')
    op.drop_table('grading_bands')
    op.drop_table('grading_schemes')
