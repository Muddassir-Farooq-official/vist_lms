"""Daily review of money changes: which entries an administrator has checked, and a faster look-up by action.

Revision ID: a1c3e5f7b9d2
Revises: f0b2c4d6e8a1
Create Date: 2026-11-08 14:00:00.000000

ADDITIVE and safe to run while the old version of the app is still serving:
- new, empty table money_review_checks;
- a new index on audit_logs (action, created_at), built CONCURRENTLY so writing to the activity log is never blocked
  while it is built (it can take a little while on a large log).
No existing row is changed. Downgrade drops both.
"""
from alembic import op
import sqlalchemy as sa

revision = "a1c3e5f7b9d2"
down_revision = "f0b2c4d6e8a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "money_review_checks",
        sa.Column("audit_log_id", sa.Uuid(), nullable=False),
        sa.Column("checked_by", sa.Uuid(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(["audit_log_id"], ["audit_logs.id"], name=op.f("fk_money_review_checks_audit_log_id_audit_logs"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["checked_by"], ["users.id"], name=op.f("fk_money_review_checks_checked_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("audit_log_id", name=op.f("pk_money_review_checks")),
    )
    with op.get_context().autocommit_block():
        op.create_index("ix_audit_logs_action_created", "audit_logs", ["action", "created_at"], unique=False,
                        postgresql_concurrently=True, if_not_exists=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("ix_audit_logs_action_created", table_name="audit_logs", postgresql_concurrently=True, if_exists=True)
    op.drop_table("money_review_checks")
