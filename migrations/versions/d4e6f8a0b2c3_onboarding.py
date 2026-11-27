"""onboarding: when a person's "complete your profile" reminders started

Production impact: adds one empty, nullable column, users.nudge_started_on. No existing row is changed. The first time
someone opens the portal after this update, the day is filled in; after 7 days with a profile still incomplete, their
profile page opens first after sign-in (other pages stay reachable from the menu). Nobody is blocked on the day of the
update. Downgrade drops only this column.

Revision ID: d4e6f8a0b2c3
Revises: c3d5e7f9a1b2
Create Date: 2026-11-27 17:30:00

"""
from alembic import op
import sqlalchemy as sa


revision = 'd4e6f8a0b2c3'
down_revision = 'c3d5e7f9a1b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('nudge_started_on', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'nudge_started_on')
