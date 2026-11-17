"""Self-assessments: marks of paper quizzes and class tests, kept apart from exams and results.

Production impact: creates two new, empty tables (self_assessments, self_assessment_marks). Nothing existing is read
or changed; grading, result sheets, GPA and transcripts never read these tables. Downgrade drops both tables (any
self-assessment marks typed in would be lost).

Revision ID: c8f0a2b4d6e1
Revises: b7e9a1c3d5f2
"""
from alembic import op
import sqlalchemy as sa


revision = 'c8f0a2b4d6e1'
down_revision = 'b7e9a1c3d5f2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'self_assessments',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('course_offering_id', sa.Uuid(), nullable=False),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column('kind', sa.String(length=15), nullable=False),
        sa.Column('held_on', sa.Date(), nullable=False),
        sa.Column('max_marks', sa.Numeric(6, 2), nullable=False),
        sa.Column('topic', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['course_offering_id'], ['course_offerings.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_self_assessments_course_offering_id', 'self_assessments', ['course_offering_id'])
    op.create_table(
        'self_assessment_marks',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('self_assessment_id', sa.Uuid(), nullable=False),
        sa.Column('enrollment_id', sa.Uuid(), nullable=False),
        sa.Column('obtained', sa.Numeric(6, 2), nullable=True),
        sa.Column('absent', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('remark', sa.String(length=200), nullable=True),
        sa.Column('entered_by', sa.Uuid(), nullable=True),
        sa.Column('entered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['self_assessment_id'], ['self_assessments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['enrollment_id'], ['enrollments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['entered_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('self_assessment_id', 'enrollment_id', name='uq_self_assessment_mark'),
    )
    op.create_index('ix_self_assessment_marks_self_assessment_id', 'self_assessment_marks', ['self_assessment_id'])


def downgrade() -> None:
    op.drop_index('ix_self_assessment_marks_self_assessment_id', table_name='self_assessment_marks')
    op.drop_table('self_assessment_marks')
    op.drop_index('ix_self_assessments_course_offering_id', table_name='self_assessments')
    op.drop_table('self_assessments')
