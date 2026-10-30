"""email verification before using the portal (one-time code by email), and administration email addresses that get a
copy of every notification

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0

Production impact (additive only; nothing existing is changed or removed, no table is rewritten):
  * new column users.email_verified_at (nullable, no default): instant. Every existing account starts "not verified".
    Nobody is asked to verify until an administrator turns on "Every account must verify an email address" in
    Settings -> Email, which is OFF after this migration, so deploying changes nothing for anyone by itself.
  * new table email_verification_codes (one row per code sent; starts empty). Codes are stored only as a keyed hash.
  * new columns on email_settings: require_verified_email (NOT NULL, default false), admin_emails (text, nullable),
    admin_copy_categories (text, nullable = every kind). Instant (constant defaults, one-row table). No copies are sent
    until an administrator types at least one administration address.
  * new nullable columns on email_outbox: group_key, copy_recipients (used to send ONE copy to the administration when
    the same notice goes to many people). Nullable, no default: instant; existing rows untouched.
Safe to run while the site is up. Downgrade drops the table and the columns (who verified which address, and the
administration email list, are lost; nothing else is affected).
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_settings", sa.Column("require_verified_email", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("email_settings", sa.Column("admin_emails", sa.Text(), nullable=True))
    op.add_column("email_settings", sa.Column("admin_copy_categories", sa.Text(), nullable=True))
    op.add_column("email_outbox", sa.Column("group_key", sa.String(64), nullable=True))
    op.add_column("email_outbox", sa.Column("copy_recipients", sa.Text(), nullable=True))
    op.create_index("ix_email_outbox_group_key", "email_outbox", ["group_key"], postgresql_where=sa.text("group_key IS NOT NULL"))
    op.create_table(
        "email_verification_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_email_verification_codes_user_created", "email_verification_codes", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_email_verification_codes_user_created", table_name="email_verification_codes")
    op.drop_table("email_verification_codes")
    op.drop_index("ix_email_outbox_group_key", table_name="email_outbox")
    op.drop_column("email_outbox", "copy_recipients")
    op.drop_column("email_outbox", "group_key")
    op.drop_column("email_settings", "admin_copy_categories")
    op.drop_column("email_settings", "admin_emails")
    op.drop_column("email_settings", "require_verified_email")
    op.drop_column("users", "email_verified_at")