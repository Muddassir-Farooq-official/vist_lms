"""announcements (+ attachments, authorities, read tracking); complaints remember when review began

Revision ID: c5f1a9d7e204
Revises: a3c7e1f9d052
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c5f1a9d7e204"
down_revision = "a3c7e1f9d052"
branch_labels = None
depends_on = None

CATEGORY = ("GENERAL", "ACADEMIC", "EXAMINATION", "ADMISSIONS", "FEES", "EVENT", "HOLIDAY", "SAFETY")


def upgrade() -> None:
    # complaints: when did the administrator start looking into it? (shown to the person who filed it)
    op.add_column("complaints", sa.Column("under_review_at", sa.DateTime(timezone=True), nullable=True))

    # notifications: a category of their own for announcements
    op.execute("ALTER TYPE notification_category ADD VALUE IF NOT EXISTS 'ANNOUNCEMENT'")

    op.create_table(
        "issuing_authorities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("designation", sa.String(120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "announcements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("category", postgresql.ENUM(*CATEGORY, name="announcement_category"), nullable=False),
        sa.Column("priority", postgresql.ENUM("NORMAL", "IMPORTANT", "URGENT", name="announcement_priority"), nullable=False),
        sa.Column("issuer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("issuing_authorities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("issuer_name", sa.String(120), nullable=False),
        sa.Column("issuer_designation", sa.String(120), nullable=False),
        sa.Column("audience_students", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("audience_teachers", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("audience_staff", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("program_ids", sa.JSON(), nullable=True),
        sa.Column("status", postgresql.ENUM("DRAFT", "PUBLISHED", "ARCHIVED", name="announcement_status"), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("published_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_announcements_status", "announcements", ["status"])
    op.create_table(
        "announcement_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("announcement_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_announcement_attachments_announcement", "announcement_attachments", ["announcement_id"])
    op.create_table(
        "announcement_reads",
        sa.Column("announcement_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("announcements.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Give the existing Admin role the new permission (the seed script does this for fresh installs).
    op.execute("""
        INSERT INTO permissions (id, code, description, created_at, updated_at)
        SELECT gen_random_uuid(), 'announcement.manage', 'Write, attach and publish official announcements', now(), now()
        WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'announcement.manage')
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE r.code = 'ADMIN' AND p.code = 'announcement.manage'
          AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)
    """)


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'announcement.manage')")
    op.execute("DELETE FROM permissions WHERE code = 'announcement.manage'")
    op.drop_table("announcement_reads")
    op.drop_index("ix_announcement_attachments_announcement", table_name="announcement_attachments")
    op.drop_table("announcement_attachments")
    op.drop_index("ix_announcements_status", table_name="announcements")
    op.drop_table("announcements")
    op.drop_table("issuing_authorities")
    for name in ("announcement_status", "announcement_priority", "announcement_category"):
        op.execute(f"DROP TYPE IF EXISTS {name}")
    op.drop_column("complaints", "under_review_at")
    # (PostgreSQL cannot remove a value from the notification_category enum; it is left in place, unused.)