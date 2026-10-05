"""notifications module — in-app notifications (the real, working version of "notifications" given no SMTP/SMS provider is configured)

Revision ID: f3a8d6c25b7e
Revises: e2b7c4f91a6d
Create Date: 2026-09-14 00:00:00.000000

New standalone `notifications` table, tied to users.id directly (not a
student/employee record) so a person sees their notifications
regardless of which domain role they're acting in.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3a8d6c25b7e'
down_revision = 'e2b7c4f91a6d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'notifications',
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column(
            'category',
            sa.Enum('LIBRARY', 'FINE', 'COMPLAINT', 'FEE', 'GENERAL', name='notification_category'),
            nullable=False,
        ),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('message', sa.String(length=1000), nullable=False),
        sa.Column('link', sa.String(length=500), nullable=True),
        sa.Column('is_read', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notifications_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications')),
    )
    op.alter_column('notifications', 'is_read', server_default=None)
    op.create_index('ix_notifications_user_id', 'notifications', ['user_id'], unique=False)
    op.create_index('ix_notifications_is_read', 'notifications', ['is_read'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_notifications_is_read', table_name='notifications')
    op.drop_index('ix_notifications_user_id', table_name='notifications')
    op.drop_table('notifications')
    op.execute("DROP TYPE IF EXISTS notification_category")