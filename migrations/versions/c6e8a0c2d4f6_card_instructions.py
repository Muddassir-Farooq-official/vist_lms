"""The back of ID cards: instructions kept with the institute profile.

Production impact: one new column institute_profile.card_instructions (text, NULL). institute_profile has one row; adding
a NULL column does not rewrite it, and NULL means "use the ready-made instructions". Nothing else changes.
Downgrade drops the column (instructions typed in Settings would be lost; cards go back to the ready-made ones).

Revision ID: c6e8a0c2d4f6
Revises: b5d7f9b1c3e5
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c6e8a0c2d4f6'
down_revision = 'b5d7f9b1c3e5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('institute_profile', sa.Column('card_instructions', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('institute_profile', 'card_instructions')
