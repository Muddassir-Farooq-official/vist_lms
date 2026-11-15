"""Supplementary re-sit with two papers (a subject failed in both theory and practical).

Production impact: adds two NULL columns to supplementary_attempts (second_assessment_id -> offering_assessments,
CASCADE; second_obtained_marks). Existing attempts keep NULL and work exactly as before (one paper).
Downgrade drops the two columns (second-paper marks typed in would be lost).

Revision ID: b7e9a1c3d5f2
Revises: f9b1d3e5a7c9
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7e9a1c3d5f2'
down_revision = 'f9b1d3e5a7c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('supplementary_attempts', sa.Column('second_assessment_id', sa.Uuid(), nullable=True))
    op.add_column('supplementary_attempts', sa.Column('second_obtained_marks', sa.Numeric(6, 2), nullable=True))
    op.create_foreign_key('fk_supplementary_second_paper', 'supplementary_attempts', 'offering_assessments', ['second_assessment_id'], ['id'], ondelete='CASCADE')


def downgrade() -> None:
    op.drop_constraint('fk_supplementary_second_paper', 'supplementary_attempts', type_='foreignkey')
    op.drop_column('supplementary_attempts', 'second_obtained_marks')
    op.drop_column('supplementary_attempts', 'second_assessment_id')
