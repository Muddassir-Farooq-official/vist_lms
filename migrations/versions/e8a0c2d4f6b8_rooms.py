"""Rooms: the institute's classrooms, labs and halls, with seats.

Production impact: creates one new, empty table (rooms). Nothing existing is read or changed. While the table is empty
the timetable accepts any typed room exactly as today; the stricter checks start only after an administrator adds the
first room (Timetable -> Rooms). Downgrade drops the table (the room list typed in would be lost; timetables keep their
room names, which are plain text).

Revision ID: e8a0c2d4f6b8
Revises: d7f9b1c3e5a7
"""
from alembic import op
import sqlalchemy as sa


revision = 'e8a0c2d4f6b8'
down_revision = 'd7f9b1c3e5a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'rooms',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=50), nullable=False),
        sa.Column('key', sa.String(length=50), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('seats', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )


def downgrade() -> None:
    op.drop_table('rooms')
