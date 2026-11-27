"""staff leaving: how they left and when notice was given

Production impact: adds two empty, nullable columns to employees: leaving_type (RESIGNED | TERMINATED | CONTRACT_ENDED)
and notice_given_on. No existing row is changed. From now on payroll pays a person who left during the month for the
days they worked (before, they were left out of that month's payroll altogether); a resignation with less than 30
days between notice and the last day has those days' salary withheld, as the institute decided. Staff who left before
this update keep leaving_type empty and are treated as terminated by the institute (paid for days worked) if a payroll
for their last month is prepared again. Downgrade drops only these two columns.

Revision ID: c3d5e7f9a1b2
Revises: b7c1e2d4f9a0
Create Date: 2026-11-27 16:30:00

"""
from alembic import op
import sqlalchemy as sa


revision = 'c3d5e7f9a1b2'
down_revision = 'b7c1e2d4f9a0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('employees', sa.Column('leaving_type', sa.String(length=20), nullable=True))
    op.add_column('employees', sa.Column('notice_given_on', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('employees', 'notice_given_on')
    op.drop_column('employees', 'leaving_type')
