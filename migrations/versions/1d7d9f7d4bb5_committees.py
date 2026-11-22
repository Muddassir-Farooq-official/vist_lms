"""committees: members, meetings, minutes and action items

Production impact: creates four new, empty tables (committees, committee_members, committee_meetings,
committee_actions). No existing table or row is changed. The new permission committee.manage comes from the
permission seed the deploy already runs (administrators). Downgrade drops only these tables.

Revision ID: 1d7d9f7d4bb5
Revises: d612848eca81
Create Date: 2026-11-22 20:28:04.326686

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1d7d9f7d4bb5'
down_revision = 'd612848eca81'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('committees',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('purpose', sa.String(length=1000), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_committees_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_committees')),
    sa.UniqueConstraint('name', name='uq_committees_name')
    )
    op.create_table('committee_meetings',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('committee_id', sa.Uuid(), nullable=False),
    sa.Column('held_on', sa.Date(), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=True),
    sa.Column('place', sa.String(length=120), nullable=True),
    sa.Column('agenda', sa.Text(), nullable=False),
    sa.Column('minutes', sa.Text(), nullable=True),
    sa.Column('decisions', sa.Text(), nullable=True),
    sa.Column('attended', sa.String(length=2000), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finalised_by', sa.Uuid(), nullable=True),
    sa.Column('finalised_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['committee_id'], ['committees.id'], name=op.f('fk_committee_meetings_committee_id_committees'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_committee_meetings_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['finalised_by'], ['users.id'], name=op.f('fk_committee_meetings_finalised_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_committee_meetings'))
    )
    op.create_index('ix_committee_meetings_committee', 'committee_meetings', ['committee_id', 'held_on'], unique=False)
    op.create_table('committee_members',
    sa.Column('committee_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.String(length=10), nullable=False),
    sa.ForeignKeyConstraint(['committee_id'], ['committees.id'], name=op.f('fk_committee_members_committee_id_committees'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_committee_members_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('committee_id', 'user_id', name=op.f('pk_committee_members'))
    )
    op.create_index(op.f('ix_committee_members_user_id'), 'committee_members', ['user_id'], unique=False)
    op.create_table('committee_actions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('meeting_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('owner_id', sa.Uuid(), nullable=False),
    sa.Column('due_on', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('done_note', sa.String(length=500), nullable=True),
    sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reminded_on', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['meeting_id'], ['committee_meetings.id'], name=op.f('fk_committee_actions_meeting_id_committee_meetings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_committee_actions_owner_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_committee_actions'))
    )
    op.create_index(op.f('ix_committee_actions_meeting_id'), 'committee_actions', ['meeting_id'], unique=False)
    op.create_index('ix_committee_actions_owner', 'committee_actions', ['owner_id', 'status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_committee_actions_owner', table_name='committee_actions')
    op.drop_index(op.f('ix_committee_actions_meeting_id'), table_name='committee_actions')
    op.drop_table('committee_actions')
    op.drop_index(op.f('ix_committee_members_user_id'), table_name='committee_members')
    op.drop_table('committee_members')
    op.drop_index('ix_committee_meetings_committee', table_name='committee_meetings')
    op.drop_table('committee_meetings')
    op.drop_table('committees')
