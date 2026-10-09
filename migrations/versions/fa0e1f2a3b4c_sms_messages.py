"""sms: outgoing SMS log/queue (sms_messages) and the sms.manage permission (ADMIN)

Revision ID: fa0e1f2a3b4c
Revises: f9d0e1f2a3b4
"""
import sqlalchemy as sa
from alembic import op

revision = "fa0e1f2a3b4c"
down_revision = "f9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    status = sa.Enum("QUEUED", "SENT", "FAILED", "LOGGED", "SKIPPED", name="sms_status")
    op.create_table(
        "sms_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("to_number", sa.String(20), nullable=False),
        sa.Column("recipient", sa.String(20), nullable=False),
        sa.Column("body", sa.String(320), nullable=False),
        sa.Column("event", sa.String(40), nullable=False),
        sa.Column("student_id", sa.Uuid(), sa.ForeignKey("students.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", status, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("provider_reference", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sms_messages_status_created", "sms_messages", ["status", "created_at"])
    op.create_index("ix_sms_messages_student_event", "sms_messages", ["student_id", "event"])
    op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), 'sms.manage', "
                       "'See and manage outgoing SMS', now(), now() WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'sms.manage')"))
    op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
                       "WHERE r.code = 'ADMIN' AND p.code = 'sms.manage' AND NOT EXISTS "
                       "(SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)"))


def downgrade() -> None:
    op.drop_table("sms_messages")
    sa.Enum(name="sms_status").drop(op.get_bind(), checkfirst=True)
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'sms.manage')")
    op.execute("DELETE FROM permissions WHERE code = 'sms.manage'")