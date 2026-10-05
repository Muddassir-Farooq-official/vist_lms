"""subject/course-level fees — charged per enrollment (including retakes), per approved decision

Revision ID: c7e3f9a52d6b
Revises: b4f2a8d17c9e
Create Date: 2026-09-15 00:00:00.000000

Approved decisions (this conversation):
- A subject-level fee is charged on EVERY enrollment attempt, including
  retakes — not just the first time.
- It applies only to students actually enrolled in that specific course,
  not everyone in the matching program/semester.

This is why course-scoped fees can't reuse the existing
(fee_structure_id, payer_type, payer_id) uniqueness — that would block
a second charge for a retake outright. Fixed with two PARTIAL unique
indexes instead of one constraint, replacing the old one:
- Non-course fees (enrollment_id IS NULL): still exactly one assignment
  per (structure, payer) — unchanged behavior for every existing fee.
- Course fees (enrollment_id IS NOT NULL): one assignment per
  (structure, payer, enrollment) — a retake is a NEW enrollment row, so
  it naturally gets its own assignment without touching the first one.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c7e3f9a52d6b'
down_revision = 'b4f2a8d17c9e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- fee_structures: optional course scope ---
    op.add_column('fee_structures', sa.Column('course_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        'fk_fee_structures_course_id_courses', 'fee_structures', 'courses', ['course_id'], ['id'], ondelete='SET NULL'
    )

    # --- fee_assignments: optional link to the specific enrollment that caused the charge ---
    op.add_column('fee_assignments', sa.Column('enrollment_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        'fk_fee_assignments_enrollment_id_enrollments', 'fee_assignments', 'enrollments',
        ['enrollment_id'], ['id'], ondelete='SET NULL',
    )

    # Replace the single unique constraint with two partial ones.
    op.drop_constraint('uq_fee_assignments_structure_payer', 'fee_assignments', type_='unique')
    op.create_index(
        'uq_fee_assignments_structure_payer_no_enrollment', 'fee_assignments',
        ['fee_structure_id', 'payer_type', 'payer_id'], unique=True,
        postgresql_where=sa.text('enrollment_id IS NULL'),
    )
    op.create_index(
        'uq_fee_assignments_structure_payer_enrollment', 'fee_assignments',
        ['fee_structure_id', 'payer_type', 'payer_id', 'enrollment_id'], unique=True,
        postgresql_where=sa.text('enrollment_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_fee_assignments_structure_payer_enrollment', table_name='fee_assignments')
    op.drop_index('uq_fee_assignments_structure_payer_no_enrollment', table_name='fee_assignments')
    op.create_unique_constraint(
        'uq_fee_assignments_structure_payer', 'fee_assignments', ['fee_structure_id', 'payer_type', 'payer_id']
    )

    op.drop_constraint('fk_fee_assignments_enrollment_id_enrollments', 'fee_assignments', type_='foreignkey')
    op.drop_column('fee_assignments', 'enrollment_id')

    op.drop_constraint('fk_fee_structures_course_id_courses', 'fee_structures', type_='foreignkey')
    op.drop_column('fee_structures', 'course_id')