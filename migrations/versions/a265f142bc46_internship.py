"""internship year: internships, placements, duty logs, leave and evaluations

Production impact: creates five new, empty tables (internships, intern_placements, intern_duty_logs, intern_leaves,
intern_evaluations). No existing table or row is changed. Graduation lists now also check an ongoing internship;
students who never had an internship are not affected. Downgrade drops only these tables.

Revision ID: a265f142bc46
Revises: 1d7d9f7d4bb5
Create Date: 2026-11-22 20:34:38.061965

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a265f142bc46'
down_revision = '1d7d9f7d4bb5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('internships',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('required_hours', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('completed_on', sa.Date(), nullable=True),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_internships_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_internships_decided_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_internships_student_id_students'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_internships'))
    )
    op.create_index(op.f('ix_internships_student_id'), 'internships', ['student_id'], unique=False)
    op.create_index('uq_internships_one_open', 'internships', ['student_id'], unique=True, postgresql_where=sa.text("status = 'ONGOING'"))
    op.create_table('intern_leaves',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('internship_id', sa.Uuid(), nullable=False),
    sa.Column('from_date', sa.Date(), nullable=False),
    sa.Column('to_date', sa.Date(), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('status', sa.String(length=9), nullable=False),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_note', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_intern_leaves_decided_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['internship_id'], ['internships.id'], name=op.f('fk_intern_leaves_internship_id_internships'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_intern_leaves'))
    )
    op.create_index(op.f('ix_intern_leaves_internship_id'), 'intern_leaves', ['internship_id'], unique=False)
    op.create_table('intern_placements',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('internship_id', sa.Uuid(), nullable=False),
    sa.Column('unit_id', sa.Uuid(), nullable=False),
    sa.Column('supervisor_id', sa.Uuid(), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.ForeignKeyConstraint(['internship_id'], ['internships.id'], name=op.f('fk_intern_placements_internship_id_internships'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['supervisor_id'], ['clinical_supervisors.id'], name=op.f('fk_intern_placements_supervisor_id_clinical_supervisors'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['unit_id'], ['clinical_units.id'], name=op.f('fk_intern_placements_unit_id_clinical_units'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_intern_placements'))
    )
    op.create_index(op.f('ix_intern_placements_internship_id'), 'intern_placements', ['internship_id'], unique=False)
    op.create_index(op.f('ix_intern_placements_supervisor_id'), 'intern_placements', ['supervisor_id'], unique=False)
    op.create_table('intern_duty_logs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('internship_id', sa.Uuid(), nullable=False),
    sa.Column('placement_id', sa.Uuid(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('shift', sa.String(length=8), nullable=False),
    sa.Column('hours', sa.Numeric(precision=4, scale=1), nullable=False),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('status', sa.String(length=9), nullable=False),
    sa.Column('reject_note', sa.String(length=300), nullable=True),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_intern_duty_logs_decided_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['internship_id'], ['internships.id'], name=op.f('fk_intern_duty_logs_internship_id_internships'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['placement_id'], ['intern_placements.id'], name=op.f('fk_intern_duty_logs_placement_id_intern_placements'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_intern_duty_logs')),
    sa.UniqueConstraint('internship_id', 'day', 'shift', name='uq_intern_duty_logs_day_shift')
    )
    op.create_index(op.f('ix_intern_duty_logs_internship_id'), 'intern_duty_logs', ['internship_id'], unique=False)
    op.create_index(op.f('ix_intern_duty_logs_placement_id'), 'intern_duty_logs', ['placement_id'], unique=False)
    op.create_table('intern_evaluations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('internship_id', sa.Uuid(), nullable=False),
    sa.Column('placement_id', sa.Uuid(), nullable=True),
    sa.Column('scores', sa.JSON(), nullable=False),
    sa.Column('average', sa.Numeric(precision=3, scale=2), nullable=False),
    sa.Column('comment', sa.String(length=1000), nullable=True),
    sa.Column('evaluated_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['evaluated_by'], ['users.id'], name=op.f('fk_intern_evaluations_evaluated_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['internship_id'], ['internships.id'], name=op.f('fk_intern_evaluations_internship_id_internships'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['placement_id'], ['intern_placements.id'], name=op.f('fk_intern_evaluations_placement_id_intern_placements'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_intern_evaluations'))
    )
    op.create_index(op.f('ix_intern_evaluations_internship_id'), 'intern_evaluations', ['internship_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_intern_evaluations_internship_id'), table_name='intern_evaluations')
    op.drop_table('intern_evaluations')
    op.drop_index(op.f('ix_intern_duty_logs_placement_id'), table_name='intern_duty_logs')
    op.drop_index(op.f('ix_intern_duty_logs_internship_id'), table_name='intern_duty_logs')
    op.drop_table('intern_duty_logs')
    op.drop_index(op.f('ix_intern_placements_supervisor_id'), table_name='intern_placements')
    op.drop_index(op.f('ix_intern_placements_internship_id'), table_name='intern_placements')
    op.drop_table('intern_placements')
    op.drop_index(op.f('ix_intern_leaves_internship_id'), table_name='intern_leaves')
    op.drop_table('intern_leaves')
    op.drop_index('uq_internships_one_open', table_name='internships', postgresql_where=sa.text("status = 'ONGOING'"))
    op.drop_index(op.f('ix_internships_student_id'), table_name='internships')
    op.drop_table('internships')
