"""Institute profile: name, contact details, logos, colour, bank details, signatory and document footer.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-10-25 00:00:00.000000

ADDITIVE and safe while the old version is serving: one new table (empty; until someone saves in Settings ->
Institute, the name and logo come from .env exactly as before) and one new permission, institute.profile.manage,
granted to ADMIN. Downgrade drops both (uploaded logo files stay in var/storage/branding).
"""
from alembic import op
import sqlalchemy as sa

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "institute_profile",
        sa.Column("id", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("short_name", sa.String(length=40), nullable=False),
        sa.Column("tagline", sa.String(length=200), nullable=True),
        sa.Column("address", sa.String(length=300), nullable=True),
        sa.Column("city", sa.String(length=80), nullable=True),
        sa.Column("phone", sa.String(length=60), nullable=True),
        sa.Column("email", sa.String(length=150), nullable=True),
        sa.Column("website", sa.String(length=150), nullable=True),
        sa.Column("registration_no", sa.String(length=120), nullable=True),
        sa.Column("primary_color", sa.String(length=7), nullable=True),
        sa.Column("document_footer", sa.String(length=300), nullable=True),
        sa.Column("bank_name", sa.String(length=120), nullable=True),
        sa.Column("bank_account_title", sa.String(length=150), nullable=True),
        sa.Column("bank_account_no", sa.String(length=60), nullable=True),
        sa.Column("bank_branch", sa.String(length=150), nullable=True),
        sa.Column("signatory_name", sa.String(length=120), nullable=True),
        sa.Column("signatory_title", sa.String(length=120), nullable=True),
        sa.Column("logo_key", sa.String(length=255), nullable=True),
        sa.Column("logo_dark_key", sa.String(length=255), nullable=True),
        sa.Column("favicon_key", sa.String(length=255), nullable=True),
        sa.Column("signature_key", sa.String(length=255), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name=op.f("fk_institute_profile_updated_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_institute_profile")),
    )
    op.execute(sa.text(
        "INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), 'institute.profile.manage', "
        "'Change the institute name, logos, colour, contact and bank details shown on every page and document', now(), now() "
        "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'institute.profile.manage')"))
    op.execute(sa.text(
        "INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
        "WHERE r.code = 'ADMIN' AND p.code = 'institute.profile.manage' "
        "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)"))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'institute.profile.manage')")
    op.execute("DELETE FROM permissions WHERE code = 'institute.profile.manage'")
    op.drop_table("institute_profile")
