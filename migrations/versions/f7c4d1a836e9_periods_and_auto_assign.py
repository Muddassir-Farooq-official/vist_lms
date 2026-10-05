"""periods and timetable slot auto-assign support

Revision ID: f7c4d1a836e9
Revises: e6b3a9f27d5c
Create Date: 2026-09-25 00:00:00.000000

New Period table (institute-wide daily periods, admin-defined once —
'Period 3, 10:00-10:50') and a nullable period_id on timetable_slots,
set when a slot is created from a period (manual creation or the new
auto-assign endpoint both go through one now). start_time/end_time on
timetable_slots remain the authoritative fields everywhere else reads
-- period_id is a display/provenance link, not a second source of
truth, and stays nullable so deleting a Period later doesn't cascade
onto slots that were created from it.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f7c4d1a836e9'
down_revision = 'e6b3a9f27d5c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'periods',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('period_number', sa.Integer(), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_periods')),
        sa.UniqueConstraint('period_number', name='uq_periods_period_number'),
    )

    op.add_column('timetable_slots', sa.Column('period_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f('fk_timetable_slots_period_id_periods'), 'timetable_slots', 'periods',
        ['period_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(op.f('fk_timetable_slots_period_id_periods'), 'timetable_slots', type_='foreignkey')
    op.drop_column('timetable_slots', 'period_id')
    op.drop_table('periods')