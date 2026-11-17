"""Periods: a kind (class, break, zero period, library/self-study), an optional name, and the weekdays it applies to.

Production impact: adds three columns to the small `periods` table: `kind` (NOT NULL, default 'CLASS', so every
existing period stays an ordinary class period), `label` (NULL) and `days` (NULL = every day, as today). It drops
the unique constraint on period_number so that, e.g., Friday can have its own shorter "Period 3"; the service still
refuses two periods with the same number on the same day. No existing row, slot or class session is changed.
Downgrade removes the three columns and restores the unique constraint; it stops with a message (changing nothing)
if two periods now share a number.

Revision ID: d4e6f8a0b2c4
Revises: c8f0a2b4d6e1
"""
from alembic import op
import sqlalchemy as sa


revision = 'd4e6f8a0b2c4'
down_revision = 'c8f0a2b4d6e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('periods', sa.Column('kind', sa.String(length=10), nullable=False, server_default='CLASS'))
    op.add_column('periods', sa.Column('label', sa.String(length=40), nullable=True))
    op.add_column('periods', sa.Column('days', sa.String(length=80), nullable=True))
    op.drop_constraint('uq_periods_period_number', 'periods', type_='unique')


def downgrade() -> None:
    dupes = op.get_bind().execute(sa.text(
        "SELECT period_number FROM periods GROUP BY period_number HAVING count(*) > 1")).fetchall()
    if dupes:
        raise RuntimeError("Cannot downgrade: several periods share a number (%s). Renumber or delete the day-specific "
                           "periods first." % ", ".join(str(r[0]) for r in dupes))
    op.create_unique_constraint('uq_periods_period_number', 'periods', ['period_number'])
    op.drop_column('periods', 'days')
    op.drop_column('periods', 'label')
    op.drop_column('periods', 'kind')
