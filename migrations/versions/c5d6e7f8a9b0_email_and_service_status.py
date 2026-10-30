"""email: SMTP settings edited on the Settings page, an outbox so every notification is also emailed, a per-person
"email me" switch, and service heartbeats for the "background services" status on the dashboard

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9

Production impact (additive only; nothing existing is changed or removed):
  * new table email_settings (one row once an administrator saves the email settings; the SMTP password is stored
    encrypted with a key derived from JWT_SECRET, never in plain text);
  * new table email_outbox (emails waiting to be sent / sent / failed; filled from now on, starts empty);
  * new table service_heartbeats (when the background sender last ran; starts empty);
  * new column users.email_notifications, NOT NULL with server default true: PostgreSQL 11+ adds a column with a
    constant default without rewriting the table, so it is instant even on a large users table. Every existing person
    starts with email on (they can switch it off on My Profile). Emails only go out once an administrator turns email on.
Safe to run while the site is up. Downgrade drops the three tables and the column (queued emails and saved email
settings are lost; nothing else is affected).
"""
import sqlalchemy as sa
from alembic import op

revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("host", sa.String(255), nullable=True),
        sa.Column("port", sa.Integer(), nullable=False, server_default="587"),
        sa.Column("security", sa.String(10), nullable=False, server_default="starttls"),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("password_enc", sa.Text(), nullable=True),
        sa.Column("from_name", sa.String(120), nullable=True),
        sa.Column("from_email", sa.String(255), nullable=True),
        sa.Column("reply_to", sa.String(255), nullable=True),
        sa.Column("notify_by_email", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_error", sa.String(500), nullable=True),
        sa.Column("updated_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_email_settings_single_row"),
    )
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notification_id", sa.Uuid(), sa.ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True),
        sa.Column("to_email", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False, server_default="NOTIFICATION"),
        sa.Column("status", sa.String(10), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("next_try_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_email_outbox_status_next", "email_outbox", ["status", "next_try_at"])
    op.create_table(
        "service_heartbeats",
        sa.Column("name", sa.String(40), primary_key=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.String(500), nullable=True),
    )
    op.add_column("users", sa.Column("email_notifications", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("users", "email_notifications")
    op.drop_table("service_heartbeats")
    op.drop_index("ix_email_outbox_status_next", table_name="email_outbox")
    op.drop_table("email_outbox")
    op.drop_table("email_settings")