"""Records: the register of certificates and ID cards (checkable online), and documents on file.

Revision ID: d9f5a6b7c890
Revises: c8e4f5a6b789
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d9f5a6b7c890"
down_revision = "c8e4f5a6b789"
branch_labels = None
depends_on = None

PERMS = (
    ("records.issue", "Issue certificates and ID cards; cancel them", ["ADMIN"]),
    ("records.documents", "Add, check and archive documents on student and staff files", ["ADMIN"]),
)


def _stamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "issued_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("serial_no", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("owner_type", sa.String(10), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("owner_name", sa.String(200), nullable=False),
        sa.Column("verification_code", sa.String(16), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("issued_by", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        *_stamps(),
        sa.ForeignKeyConstraint(["issued_by"], ["users.id"], name="fk_issued_documents_issued_by_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_issued_documents"),
        sa.UniqueConstraint("serial_no", name="uq_issued_documents_serial_no"),
        sa.UniqueConstraint("verification_code", name="uq_issued_documents_verification_code"),
    )
    op.create_index("ix_issued_documents_owner", "issued_documents", ["owner_type", "owner_id"])
    op.create_table(
        "person_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_type", sa.String(10), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
        sa.Column("verified_by", sa.Uuid(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archive_reason", sa.String(300), nullable=True),
        *_stamps(),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], name="fk_person_documents_uploaded_by_users", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["verified_by"], ["users.id"], name="fk_person_documents_verified_by_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_person_documents"),
    )
    op.create_index("ix_person_documents_owner", "person_documents", ["owner_type", "owner_id"])
    # the certificate register and documents are history too
    for t in ("issued_documents", "person_documents"):
        op.execute(f"CREATE TRIGGER trg_{t}_keep_history BEFORE DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION vist_keep_history()")
    for code, description, roles in PERMS:
        op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
                           "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=description))
        for role in roles:
            op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
                               "WHERE r.code = :r AND p.code = :c AND NOT EXISTS "
                               "(SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(r=role, c=code))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('records.issue', 'records.documents'))")
    op.execute("DELETE FROM permissions WHERE code IN ('records.issue', 'records.documents')")
    for t in ("issued_documents", "person_documents"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{t}_keep_history ON {t}")
    op.drop_index("ix_person_documents_owner", table_name="person_documents")
    op.drop_table("person_documents")
    op.drop_index("ix_issued_documents_owner", table_name="issued_documents")
    op.drop_table("issued_documents")