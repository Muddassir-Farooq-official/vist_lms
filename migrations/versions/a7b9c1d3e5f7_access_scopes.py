"""access scopes: limit a person to some departments and/or programs

Production impact: one new, empty table (access_scopes). Nobody is limited until an administrator sets limits on Access
control -> People; Heads of Department keep their own-department rule as before. Downgrade drops the table.

Revision ID: a7b9c1d3e5f7
Revises: f6a8b0c2d4e6
Create Date: 2026-11-27 23:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = 'a7b9c1d3e5f7'
down_revision = 'f6a8b0c2d4e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'access_scopes',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE', name='fk_access_scopes_user_id_users'), nullable=False),
        sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('value', sa.String(120), nullable=False),
        sa.Column('created_by', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL', name='fk_access_scopes_created_by_users'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_access_scopes'),
        sa.UniqueConstraint('user_id', 'kind', 'value', name='uq_access_scopes_user_kind_value'),
        sa.CheckConstraint("kind IN ('DEPARTMENT', 'PROGRAM')", name='ck_access_scopes_kind'),
    )
    op.create_index('ix_access_scopes_user_id', 'access_scopes', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_access_scopes_user_id', table_name='access_scopes')
    op.drop_table('access_scopes')
