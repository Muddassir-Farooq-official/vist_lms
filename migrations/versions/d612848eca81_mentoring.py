"""student mentoring: mentors and their private meeting notes

Production impact: creates two new, empty tables (mentor_assignments, mentor_meetings). No existing table or row is
changed. The new permission mentoring.manage comes from the permission seed the deploy already runs (administrators).
Downgrade drops only these tables (and any mentors and notes entered since).

Revision ID: d612848eca81
Revises: 84f696dfc4f2
Create Date: 2026-11-22 20:21:40.402260

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd612848eca81'
down_revision = '84f696dfc4f2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('mentor_assignments',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('mentor_id', sa.Uuid(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('assigned_by', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], name=op.f('fk_mentor_assignments_assigned_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['mentor_id'], ['employees.id'], name=op.f('fk_mentor_assignments_mentor_id_employees'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_mentor_assignments_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_mentor_assignments'))
    )
    op.create_index('ix_mentor_assignments_mentor', 'mentor_assignments', ['mentor_id'], unique=False)
    op.create_index('uq_mentor_assignments_one_current', 'mentor_assignments', ['student_id'], unique=True, postgresql_where=sa.text('end_date IS NULL'))
    op.create_table('mentor_meetings',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('mentor_id', sa.Uuid(), nullable=False),
    sa.Column('met_on', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('actions', sa.String(length=1000), nullable=True),
    sa.Column('follow_up_on', sa.Date(), nullable=True),
    sa.Column('referral_status', sa.String(length=10), nullable=True),
    sa.Column('referral_reason', sa.String(length=500), nullable=True),
    sa.Column('resolution', sa.String(length=1000), nullable=True),
    sa.Column('resolved_by', sa.Uuid(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('is_deleted', sa.Boolean(), server_default='false', nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_mentor_meetings_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['mentor_id'], ['employees.id'], name=op.f('fk_mentor_meetings_mentor_id_employees'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], name=op.f('fk_mentor_meetings_resolved_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_mentor_meetings_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_mentor_meetings'))
    )
    op.create_index('ix_mentor_meetings_referral', 'mentor_meetings', ['referral_status'], unique=False)
    op.create_index('ix_mentor_meetings_student', 'mentor_meetings', ['student_id', 'met_on'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_mentor_meetings_student', table_name='mentor_meetings')
    op.drop_index('ix_mentor_meetings_referral', table_name='mentor_meetings')
    op.drop_table('mentor_meetings')
    op.drop_index('uq_mentor_assignments_one_current', table_name='mentor_assignments', postgresql_where=sa.text('end_date IS NULL'))
    op.drop_index('ix_mentor_assignments_mentor', table_name='mentor_assignments')
    op.drop_table('mentor_assignments')
