"""theory + lab: lab credit hours in the course catalog, and labs within a section on the timetable

Production impact: two new columns, no existing value changes.
  * courses.lab_credit_hours: NOT NULL, server default 0. Every existing course gets 0 (no lab), so credit hours,
    GPA, fees, credit-hour limits and the timetable behave exactly as before until an administrator enters a lab
    split (e.g. 3+1) on a course. PostgreSQL 11+ adds a column with a constant default without rewriting the table.
  * course_offerings.lab_classes_per_week: nullable, NULL for every row (= as the catalog says, which is 0 labs for
    every existing course). "Lab (double)" sections (double_period) are unchanged: every class stays a lab.
Both are quick metadata changes; no long lock. Downgrade drops both columns.

Revision ID: b8c0d2e4f6a8
Revises: a7b9c1d3e5f7
Create Date: 2026-11-28 22:30:00

"""
from alembic import op
import sqlalchemy as sa


revision = 'b8c0d2e4f6a8'
down_revision = 'a7b9c1d3e5f7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('courses', sa.Column('lab_credit_hours', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('course_offerings', sa.Column('lab_classes_per_week', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('course_offerings', 'lab_classes_per_week')
    op.drop_column('courses', 'lab_credit_hours')
