"""visiting faculty: pay per lecture

Production impact: adds one empty, nullable column, employee_payroll_settings.lecture_rate. No existing row is changed
and nobody's pay changes until a rate is set for a visiting teacher (employment type VISITING). Downgrade drops only
this column.

Revision ID: b7c1e2d4f9a0
Revises: a265f142bc46
Create Date: 2026-11-27 15:10:00

"""
from alembic import op
import sqlalchemy as sa


revision = 'b7c1e2d4f9a0'
down_revision = 'a265f142bc46'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('employee_payroll_settings', sa.Column('lecture_rate', sa.Numeric(precision=10, scale=2), nullable=True))


def downgrade() -> None:
    op.drop_column('employee_payroll_settings', 'lecture_rate')
