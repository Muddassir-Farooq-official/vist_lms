"""Exam seating and room invigilation.

Production impact: two new empty tables (exam_seats, exam_room_duties) and two new nullable columns on rooms
(exam_rows, exam_cols: empty for every existing room). No existing row changes. The existing exam_duty_role enum type
is reused, not created again.

Revision ID: 3e2e573a6780
Revises: bdfd05173b5a
Create Date: 2026-11-22 10:03:39.947300

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '3e2e573a6780'
down_revision = 'bdfd05173b5a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('exam_room_duties',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('schedule_id', sa.Uuid(), nullable=False),
    sa.Column('room_id', sa.Uuid(), nullable=False),
    sa.Column('exam_date', sa.Date(), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=False),
    sa.Column('end_time', sa.Time(), nullable=False),
    sa.Column('employee_id', sa.Uuid(), nullable=False),
    sa.Column('role', postgresql.ENUM('IN_CHARGE', 'ASSISTANT', name='exam_duty_role', create_type=False), nullable=False),
    sa.Column('assigned_by', sa.Uuid(), nullable=True),
    sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('told_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], name=op.f('fk_exam_room_duties_assigned_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_exam_room_duties_employee_id_employees'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], name=op.f('fk_exam_room_duties_room_id_rooms'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['schedule_id'], ['exam_schedules.id'], name=op.f('fk_exam_room_duties_schedule_id_exam_schedules'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_exam_room_duties')),
    sa.UniqueConstraint('exam_date', 'start_time', 'room_id', 'employee_id', name='uq_exam_room_duties_slot_room_employee')
    )
    op.create_index('ix_exam_room_duties_employee', 'exam_room_duties', ['employee_id'], unique=False)
    op.create_index(op.f('ix_exam_room_duties_schedule_id'), 'exam_room_duties', ['schedule_id'], unique=False)
    op.create_table('exam_seats',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('schedule_id', sa.Uuid(), nullable=False),
    sa.Column('paper_id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('room_id', sa.Uuid(), nullable=False),
    sa.Column('exam_date', sa.Date(), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=False),
    sa.Column('seat_row', sa.Integer(), nullable=False),
    sa.Column('seat_col', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['paper_id'], ['exam_papers.id'], name=op.f('fk_exam_seats_paper_id_exam_papers'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], name=op.f('fk_exam_seats_room_id_rooms'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['schedule_id'], ['exam_schedules.id'], name=op.f('fk_exam_seats_schedule_id_exam_schedules'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_exam_seats_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_exam_seats')),
    sa.UniqueConstraint('exam_date', 'start_time', 'room_id', 'seat_row', 'seat_col', name='uq_exam_seats_place'),
    sa.UniqueConstraint('paper_id', 'student_id', name='uq_exam_seats_paper_student')
    )
    op.create_index(op.f('ix_exam_seats_paper_id'), 'exam_seats', ['paper_id'], unique=False)
    op.create_index('ix_exam_seats_schedule_slot', 'exam_seats', ['schedule_id', 'exam_date', 'start_time'], unique=False)
    op.create_index(op.f('ix_exam_seats_student_id'), 'exam_seats', ['student_id'], unique=False)
    op.add_column('rooms', sa.Column('exam_rows', sa.Integer(), nullable=True))
    op.add_column('rooms', sa.Column('exam_cols', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('rooms', 'exam_cols')
    op.drop_column('rooms', 'exam_rows')
    op.drop_index(op.f('ix_exam_seats_student_id'), table_name='exam_seats')
    op.drop_index('ix_exam_seats_schedule_slot', table_name='exam_seats')
    op.drop_index(op.f('ix_exam_seats_paper_id'), table_name='exam_seats')
    op.drop_table('exam_seats')
    op.drop_index(op.f('ix_exam_room_duties_schedule_id'), table_name='exam_room_duties')
    op.drop_index('ix_exam_room_duties_employee', table_name='exam_room_duties')
    op.drop_table('exam_room_duties')
