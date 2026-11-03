"""Top bars: strips across the top of every page that the administration shows to chosen people.

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-11-03 00:00:00.000000

Everything here is ADDITIVE and safe to run while the old version of the app is still serving:
- two new tables: top_banners (the bars) and top_banner_seen (who pressed the button or closed a bar).
  The old code never reads or writes them. No existing table or row is changed; no permission changes
  (managing bars uses the existing announcement.manage permission).

Downgrade drops both tables (every bar and its responses are lost; nothing else is affected).
"""
from alembic import op
import sqlalchemy as sa

revision = "b0c1d2e3f4a5"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "top_banners",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("message", sa.String(length=300), nullable=False),
        sa.Column("button_label", sa.String(length=40), nullable=True),
        sa.Column("button_url", sa.String(length=500), nullable=True),
        sa.Column("new_tab", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("tone", sa.String(length=10), server_default="blue", nullable=False),
        sa.Column("audience", sa.JSON(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("dismissible", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_top_banners_created_by_users"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name=op.f("fk_top_banners_updated_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_top_banners")),
    )
    op.create_table(
        "top_banner_seen",
        sa.Column("banner_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clicks", sa.Integer(), server_default="0", nullable=False),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["banner_id"], ["top_banners.id"], name=op.f("fk_top_banner_seen_banner_id_top_banners"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_top_banner_seen_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("banner_id", "user_id", name=op.f("pk_top_banner_seen")),
    )


def downgrade() -> None:
    op.drop_table("top_banner_seen")
    op.drop_table("top_banners")
    
