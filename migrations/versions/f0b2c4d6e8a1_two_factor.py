"""Two-step sign-in: authenticator secrets on users, and remembered devices.

Revision ID: f0b2c4d6e8a1
Revises: e9a1b3c5d7f9
Create Date: 2026-11-08 13:00:00.000000

ADDITIVE and safe to run while the old version of the app is still serving:
- three new, empty, nullable columns on users (totp_secret_enc, totp_pending_enc, totp_enabled_at);
- new, empty table trusted_devices.
No existing row is changed. Nobody is asked for a code until the new code is running; administrators then set up
their authenticator app at their next sign-in. Downgrade drops the columns and the table (two-step sign-in is off).
"""
from alembic import op
import sqlalchemy as sa

revision = "f0b2c4d6e8a1"
down_revision = "e9a1b3c5d7f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret_enc", sa.String(length=300), nullable=True))
    op.add_column("users", sa.Column("totp_pending_enc", sa.String(length=300), nullable=True))
    op.add_column("users", sa.Column("totp_enabled_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "trusted_devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_trusted_devices_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trusted_devices")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_trusted_devices_token_hash")),
    )
    op.create_index("ix_trusted_devices_user", "trusted_devices", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_trusted_devices_user", table_name="trusted_devices")
    op.drop_table("trusted_devices")
    op.drop_column("users", "totp_enabled_at")
    op.drop_column("users", "totp_pending_enc")
    op.drop_column("users", "totp_secret_enc")
