"""Phone / browser push notifications: devices that turned push on, and which notifications were pushed.

Revision ID: e3a5b7c9d1f2
Revises: d2f4a6b8c0e1
Create Date: 2026-11-04 00:00:00.000000

ADDITIVE and safe to run while the old version of the app is still serving:
- new table push_subscriptions (one row per phone / browser that turned push on);
- new NULLABLE column notifications.pushed_at (no default, no rewrite of existing rows: PostgreSQL adds it instantly);
- new index ix_notifications_created_at, built CONCURRENTLY so writes to notifications are not blocked while it builds.
Existing notifications are never pushed: only those created in the last 15 minutes are considered.

Downgrade drops the index, the column and the table (devices would have to turn push on again).
"""
from alembic import op
import sqlalchemy as sa

revision = "e3a5b7c9d1f2"
down_revision = "d2f4a6b8c0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=200), nullable=False),
        sa.Column("auth", sa.String(length=100), nullable=False),
        sa.Column("device", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failures", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_push_subscriptions_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_subscriptions")),
    )
    op.create_index("ix_push_subscriptions_user", "push_subscriptions", ["user_id"], unique=False)
    op.create_index("uq_push_subscriptions_endpoint", "push_subscriptions", ["endpoint"], unique=True)
    op.add_column("notifications", sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True))
    with op.get_context().autocommit_block():
        op.create_index("ix_notifications_created_at", "notifications", ["created_at"], unique=False,
                        postgresql_concurrently=True, if_not_exists=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("ix_notifications_created_at", table_name="notifications", postgresql_concurrently=True, if_exists=True)
    op.drop_column("notifications", "pushed_at")
    op.drop_index("uq_push_subscriptions_endpoint", table_name="push_subscriptions")
    op.drop_index("ix_push_subscriptions_user", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")