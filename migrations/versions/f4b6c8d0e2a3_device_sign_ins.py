"""Which accounts each browser is used for (to notice one person signing in to someone else's account).

Revision ID: f4b6c8d0e2a3
Revises: e3a5b7c9d1f2
Create Date: 2026-11-04 00:00:00.000000

ADDITIVE and safe to run while the old version of the app is still serving: one new table, device_sign_ins, keyed by a
random browser id (a cookie) and the account. No existing table or row is changed.

Downgrade drops the table (the history of which browser was used for which account is lost; nothing else is affected).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f4b6c8d0e2a3"
down_revision = "e3a5b7c9d1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_sign_ins",
        sa.Column("device_id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_ip", postgresql.INET(), nullable=True),
        sa.Column("sign_ins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_device_sign_ins_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id", "user_id", name=op.f("pk_device_sign_ins")),
    )
    op.create_index("ix_device_sign_ins_user", "device_sign_ins", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_device_sign_ins_user", table_name="device_sign_ins")
    op.drop_table("device_sign_ins")