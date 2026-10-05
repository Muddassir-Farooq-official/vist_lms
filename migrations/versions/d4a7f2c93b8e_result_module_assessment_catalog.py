"""result module: institute-wide assessment type catalog

Revision ID: d4a7f2c93b8e
Revises: c3f8b6d29a1e
Create Date: 2026-09-28 00:00:00.000000

Replaces the old per-offering, weight-percentage AssessmentComponent
with a two-part design, per explicit request in conversation:

1. AssessmentType — an institute-wide catalog admin defines once
   ("Quiz 1", "Mid Term", each with a FIXED max_marks that applies
   everywhere the type is used).
2. OfferingAssessment — a teacher (or admin) picks which catalog types
   apply to their own subject.

A subject's overall result is now the raw sum of obtained marks over
the raw sum of max_marks across whatever was picked — no weight
percentages to balance to 100. This is a one-way, deliberately
irreversible data change for existing marks/components, matching the
same "start fresh" precedent as the earlier timetable data wipe —
courses, programs, offerings, and enrollments themselves are
untouched; only assessment_components and student_marks are cleared
and restructured.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4a7f2c93b8e'
down_revision = 'c3f8b6d29a1e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Wipe existing marks/components first (children before parents) —
    # the FK shape is changing, there's nothing sensible to migrate.
    op.execute("DELETE FROM student_marks")
    op.execute("DELETE FROM assessment_components")

    op.create_table(
        'assessment_types',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=True),
        sa.Column('max_marks', sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_assessment_types')),
        sa.UniqueConstraint('name', name='uq_assessment_types_name'),
    )

    op.create_table(
        'offering_assessments',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('course_offering_id', sa.Uuid(), nullable=False),
        sa.Column('assessment_type_id', sa.Uuid(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['course_offering_id'], ['course_offerings.id'],
            name=op.f('fk_offering_assessments_course_offering_id_course_offerings'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['assessment_type_id'], ['assessment_types.id'],
            name=op.f('fk_offering_assessments_assessment_type_id_assessment_types'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['created_by'], ['users.id'], name=op.f('fk_offering_assessments_created_by_users'), ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_offering_assessments')),
        sa.UniqueConstraint('course_offering_id', 'assessment_type_id', name='uq_offering_assessments_offering_type'),
    )
    op.create_index(
        'ix_offering_assessments_offering_id', 'offering_assessments', ['course_offering_id'], unique=False,
    )

    op.drop_constraint('uq_student_marks_enrollment_component', 'student_marks', type_='unique')
    op.drop_constraint(
        'fk_student_marks_assessment_component_id_assessment_components', 'student_marks', type_='foreignkey',
    )
    op.drop_column('student_marks', 'assessment_component_id')

    op.drop_table('assessment_components')

    op.add_column('student_marks', sa.Column('offering_assessment_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f('fk_student_marks_offering_assessment_id_offering_assessments'), 'student_marks', 'offering_assessments',
        ['offering_assessment_id'], ['id'], ondelete='CASCADE',
    )
    op.create_unique_constraint(
        'uq_student_marks_enrollment_assessment', 'student_marks', ['enrollment_id', 'offering_assessment_id'],
    )
    op.alter_column('student_marks', 'offering_assessment_id', nullable=False)


def downgrade() -> None:
    op.add_column('student_marks', sa.Column('assessment_component_id', sa.Uuid(), nullable=True))
    op.drop_constraint('uq_student_marks_enrollment_assessment', 'student_marks', type_='unique')
    op.drop_constraint(op.f('fk_student_marks_offering_assessment_id_offering_assessments'), 'student_marks', type_='foreignkey')
    op.drop_column('student_marks', 'offering_assessment_id')

    op.create_table(
        'assessment_components',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('course_offering_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('max_marks', sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column('weight_percentage', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['course_offering_id'], ['course_offerings.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_assessment_components')),
    )
    op.create_index('ix_assessment_components_offering_id', 'assessment_components', ['course_offering_id'], unique=False)
    op.create_foreign_key(
        'fk_student_marks_assessment_component_id_assessment_components', 'student_marks', 'assessment_components',
        ['assessment_component_id'], ['id'], ondelete='CASCADE',
    )
    op.create_unique_constraint(
        'uq_student_marks_enrollment_component', 'student_marks', ['enrollment_id', 'assessment_component_id'],
    )
    op.alter_column('student_marks', 'assessment_component_id', nullable=False)

    op.drop_index('ix_offering_assessments_offering_id', table_name='offering_assessments')
    op.drop_table('offering_assessments')
    op.drop_table('assessment_types')
    # The wiped marks/components data above is deliberately not restored.