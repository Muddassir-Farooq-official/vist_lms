"""hostel mess, night roll call, transport, monthly charge drafts approved by accounts

Production impact:
- hostel_stays gets one new column, mess_fee NUMERIC(10,2) NOT NULL DEFAULT 0. With a constant default PostgreSQL
  adds it without rewriting the table; every existing stay reads 0 (not in the mess), so nothing anyone owes changes.
- fine_source enum gets the value TRANSPORT (ALTER TYPE ... ADD VALUE IF NOT EXISTS: no rewrite, existing fines untouched).
- New empty tables: hostel_roll_calls, hostel_roll_marks, transport_routes, transport_stops, transport_riders,
  charge_runs, charge_lines.
- Existing hostel charges, fines and payments are not touched. The hostel office's "charge the month" now prepares a
  draft that the accounts office approves; the administrator's direct charge (API) still works.
Downgrade drops the new tables and the mess_fee column (mess fees entered since are lost); PostgreSQL cannot remove an
enum value, so TRANSPORT stays on fine_source, unused.

Revision ID: f0cd9cb45e8a
Revises: 3abf9c1a9bdb
Create Date: 2026-11-22 12:19:43.338177

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f0cd9cb45e8a'
down_revision = '3abf9c1a9bdb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE fine_source ADD VALUE IF NOT EXISTS 'TRANSPORT'")
    op.create_table('transport_routes',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('vehicle', sa.String(length=60), nullable=True),
    sa.Column('seats', sa.Integer(), nullable=True),
    sa.Column('driver_name', sa.String(length=100), nullable=True),
    sa.Column('driver_phone', sa.String(length=30), nullable=True),
    sa.Column('monthly_fee', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('monthly_fee >= 0', name=op.f('ck_transport_routes_ck_transport_routes_fee')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transport_routes')),
    sa.UniqueConstraint('name', name='uq_transport_routes_name')
    )
    op.create_table('charge_runs',
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('month', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('students', sa.Integer(), nullable=False),
    sa.Column('total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('prepared_by', sa.Uuid(), nullable=True),
    sa.Column('prepared_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_charge_runs_decided_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['prepared_by'], ['users.id'], name=op.f('fk_charge_runs_prepared_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_charge_runs'))
    )
    op.create_index('uq_charge_runs_one_draft', 'charge_runs', ['kind', 'month'], unique=True, postgresql_where=sa.text("status = 'DRAFT'"))
    op.create_table('hostel_roll_calls',
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('taken_by', sa.Uuid(), nullable=True),
    sa.Column('taken_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['taken_by'], ['users.id'], name=op.f('fk_hostel_roll_calls_taken_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_hostel_roll_calls')),
    sa.UniqueConstraint('day', name='uq_hostel_roll_calls_day')
    )
    op.create_table('transport_stops',
    sa.Column('route_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('pickup_time', sa.Time(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['route_id'], ['transport_routes.id'], name=op.f('fk_transport_stops_route_id_transport_routes'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transport_stops')),
    sa.UniqueConstraint('route_id', 'name', name='uq_transport_stops_route_name')
    )
    op.create_index(op.f('ix_transport_stops_route_id'), 'transport_stops', ['route_id'], unique=False)
    op.create_table('charge_lines',
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('ref_id', sa.Uuid(), nullable=False),
    sa.Column('month', sa.Date(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('days', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('fine_id', sa.Uuid(), nullable=True),
    sa.Column('skipped', sa.String(length=120), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['fine_id'], ['fines.id'], name=op.f('fk_charge_lines_fine_id_fines'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['charge_runs.id'], name=op.f('fk_charge_lines_run_id_charge_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_charge_lines_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_charge_lines'))
    )
    op.create_index('ix_charge_lines_run', 'charge_lines', ['run_id'], unique=False)
    op.create_index(op.f('ix_charge_lines_student_id'), 'charge_lines', ['student_id'], unique=False)
    op.create_index('uq_charge_lines_charged_once', 'charge_lines', ['kind', 'ref_id', 'month'], unique=True, postgresql_where=sa.text('fine_id IS NOT NULL'))
    op.create_table('hostel_roll_marks',
    sa.Column('roll_call_id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('note', sa.String(length=200), nullable=True),
    sa.Column('notified', sa.Boolean(), server_default='false', nullable=False),
    sa.ForeignKeyConstraint(['roll_call_id'], ['hostel_roll_calls.id'], name=op.f('fk_hostel_roll_marks_roll_call_id_hostel_roll_calls'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_hostel_roll_marks_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('roll_call_id', 'student_id', name=op.f('pk_hostel_roll_marks'))
    )
    op.create_index(op.f('ix_hostel_roll_marks_student_id'), 'hostel_roll_marks', ['student_id'], unique=False)
    op.create_table('transport_riders',
    sa.Column('route_id', sa.Uuid(), nullable=False),
    sa.Column('stop_id', sa.Uuid(), nullable=True),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('monthly_fee', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_transport_riders_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['route_id'], ['transport_routes.id'], name=op.f('fk_transport_riders_route_id_transport_routes'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['stop_id'], ['transport_stops.id'], name=op.f('fk_transport_riders_stop_id_transport_stops'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_transport_riders_student_id_students'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transport_riders'))
    )
    op.create_index('ix_transport_riders_route', 'transport_riders', ['route_id'], unique=False)
    op.create_index(op.f('ix_transport_riders_student_id'), 'transport_riders', ['student_id'], unique=False)
    op.create_index('uq_transport_riders_one_current', 'transport_riders', ['student_id'], unique=True, postgresql_where=sa.text('end_date IS NULL'))
    op.add_column('hostel_stays', sa.Column('mess_fee', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False))


def downgrade() -> None:
    # (PostgreSQL cannot remove a value from the fine_source enum; TRANSPORT is left in place, unused.)
    op.drop_column('hostel_stays', 'mess_fee')
    op.drop_index('uq_transport_riders_one_current', table_name='transport_riders', postgresql_where=sa.text('end_date IS NULL'))
    op.drop_index(op.f('ix_transport_riders_student_id'), table_name='transport_riders')
    op.drop_index('ix_transport_riders_route', table_name='transport_riders')
    op.drop_table('transport_riders')
    op.drop_index(op.f('ix_hostel_roll_marks_student_id'), table_name='hostel_roll_marks')
    op.drop_table('hostel_roll_marks')
    op.drop_index('uq_charge_lines_charged_once', table_name='charge_lines', postgresql_where=sa.text('fine_id IS NOT NULL'))
    op.drop_index(op.f('ix_charge_lines_student_id'), table_name='charge_lines')
    op.drop_index('ix_charge_lines_run', table_name='charge_lines')
    op.drop_table('charge_lines')
    op.drop_index(op.f('ix_transport_stops_route_id'), table_name='transport_stops')
    op.drop_table('transport_stops')
    op.drop_table('hostel_roll_calls')
    op.drop_index('uq_charge_runs_one_draft', table_name='charge_runs', postgresql_where=sa.text("status = 'DRAFT'"))
    op.drop_table('charge_runs')
    op.drop_table('transport_routes')
