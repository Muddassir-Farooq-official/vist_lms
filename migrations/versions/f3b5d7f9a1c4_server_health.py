"""Server health: one table the server writes its health report into every minute (System -> Server health).

Production impact: one NEW table (server_health), empty until the server's helper writes to it (a single row, replaced
every minute). Nothing existing changes. Downgrade drops the table.

Revision ID: f3b5d7f9a1c4
Revises: e1a3c5e7b9d2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'f3b5d7f9a1c4'
down_revision = 'e1a3c5e7b9d2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('server_health',
    sa.Column('name', sa.String(length=20), nullable=False),
    sa.Column('taken_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.PrimaryKeyConstraint('name', name=op.f('pk_server_health'))
    )


def downgrade() -> None:
    op.drop_table('server_health')
