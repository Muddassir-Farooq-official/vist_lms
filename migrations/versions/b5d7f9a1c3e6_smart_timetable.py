"""Smart timetable: teachers' unavailable times / limits, timetable drafts, and three per-section settings.

Production impact:
  * three NEW tables (teacher_availability, timetable_drafts, timetable_draft_slots), empty at first;
  * course_offerings gets three columns: classes_per_week (NULL = credit hours, as today), double_period
    (NOT NULL DEFAULT false: on PostgreSQL 11+ this is a metadata-only change, no table rewrite) and home_room (NULL).
    Takes a brief lock on course_offerings while the columns are added (milliseconds).
  * The live timetable (timetable_slots, class_sessions, attendance) is not touched. Nothing changes for anyone until
    an administrator publishes a draft.
Downgrade drops the three tables and the three columns (only drafts and these settings are lost).

Revision ID: b5d7f9a1c3e6
Revises: a3c5e7f9b1d4
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'b5d7f9a1c3e6'
down_revision = 'a3c5e7f9b1d4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('timetable_drafts',
    sa.Column('terms', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('days', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=12), server_default='DRAFT', nullable=False),
    sa.Column('problems', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('seconds', sa.Float(), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('published_from', sa.Date(), nullable=True),
    sa.Column('published_by', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_timetable_drafts_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['published_by'], ['users.id'], name=op.f('fk_timetable_drafts_published_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_timetable_drafts'))
    )
    op.create_index('ix_timetable_drafts_status', 'timetable_drafts', ['status'], unique=False)
    op.create_table('teacher_availability',
    sa.Column('employee_id', sa.Uuid(), nullable=False),
    sa.Column('blocked', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('max_per_day', sa.Integer(), nullable=True),
    sa.Column('max_in_row', sa.Integer(), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('locked', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_teacher_availability_employee_id_employees'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_teacher_availability_updated_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_teacher_availability')),
    sa.UniqueConstraint('employee_id', name=op.f('uq_teacher_availability_employee_id'))
    )
    op.create_table('timetable_draft_slots',
    sa.Column('draft_id', sa.Uuid(), nullable=False),
    sa.Column('course_offering_id', sa.Uuid(), nullable=False),
    sa.Column('day', sa.String(length=10), nullable=False),
    sa.Column('period_id', sa.Uuid(), nullable=False),
    sa.Column('span', sa.Integer(), server_default='1', nullable=False),
    sa.Column('room', sa.String(length=50), nullable=False),
    sa.Column('pinned', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['course_offering_id'], ['course_offerings.id'], name=op.f('fk_timetable_draft_slots_course_offering_id_course_offerings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['draft_id'], ['timetable_drafts.id'], name=op.f('fk_timetable_draft_slots_draft_id_timetable_drafts'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['period_id'], ['periods.id'], name=op.f('fk_timetable_draft_slots_period_id_periods'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_timetable_draft_slots'))
    )
    op.create_index('ix_timetable_draft_slots_draft', 'timetable_draft_slots', ['draft_id'], unique=False)
    op.add_column('course_offerings', sa.Column('classes_per_week', sa.Integer(), nullable=True))
    op.add_column('course_offerings', sa.Column('double_period', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('course_offerings', sa.Column('home_room', sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column('course_offerings', 'home_room')
    op.drop_column('course_offerings', 'double_period')
    op.drop_column('course_offerings', 'classes_per_week')
    op.drop_index('ix_timetable_draft_slots_draft', table_name='timetable_draft_slots')
    op.drop_table('timetable_draft_slots')
    op.drop_table('teacher_availability')
    op.drop_index('ix_timetable_drafts_status', table_name='timetable_drafts')
    op.drop_table('timetable_drafts')
