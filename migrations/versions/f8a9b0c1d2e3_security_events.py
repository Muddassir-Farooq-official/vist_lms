"""Security events: refused and attack-looking attempts (Activity log -> Security).

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-11-03 00:00:00.000000

Everything here is ADDITIVE and safe to run while the old version of the app is still serving:
- one new table, security_events, with four indexes. The old code never reads or writes it.
No existing table or row is changed. Failed sign-ins are not copied: they stay in login_attempts.

Downgrade drops the table (the recorded security events are lost; nothing else is affected).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f8a9b0c1d2e3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "security_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("method", sa.String(length=10), nullable=True),
        sa.Column("path", sa.String(length=255), nullable=True),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_security_events_user_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_security_events")),
    )
    op.create_index("ix_security_events_created", "security_events", ["created_at"], unique=False)
    op.create_index("ix_security_events_kind_created", "security_events", ["kind", "created_at"], unique=False)
    op.create_index("ix_security_events_ip_created", "security_events", ["ip_address", "created_at"], unique=False)
    op.create_index("ix_security_events_user_created", "security_events", ["user_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_security_events_user_created", table_name="security_events")
    op.drop_index("ix_security_events_ip_created", table_name="security_events")
    op.drop_index("ix_security_events_kind_created", table_name="security_events")
    op.drop_index("ix_security_events_created", table_name="security_events")
    op.drop_table("security_events")