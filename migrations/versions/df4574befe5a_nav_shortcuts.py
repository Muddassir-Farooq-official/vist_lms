"""Menu shortcuts: the pages each person starred.

Production impact: one new empty table nav_shortcuts. No existing table or row changes.

Revision ID: df4574befe5a
Revises: 7917ee4cf4fb
Create Date: 2026-11-22 09:48:04.030881

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'df4574befe5a'
down_revision = '7917ee4cf4fb'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('nav_shortcuts',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('portal', sa.String(length=20), nullable=False),
    sa.Column('slug', sa.String(length=40), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_nav_shortcuts_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'portal', 'slug', name=op.f('pk_nav_shortcuts'))
    )


def downgrade() -> None:
    op.drop_table('nav_shortcuts')
