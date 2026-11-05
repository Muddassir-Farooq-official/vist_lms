"""Class cover (another teacher takes an absent teacher's class, paid on their payslip), and an index for "every class on a day".

Revision ID: a5c7e9f1b3d5
Revises: f4b6c8d0e2a3
Create Date: 2026-11-05 00:00:00.000000

ADDITIVE and safe to run while the old version of the app is still serving:
- new, empty table class_covers (nothing existing is copied or changed);
- new index ix_class_sessions_session_date on class_sessions, built CONCURRENTLY so attendance marking is never blocked
  while it builds (the Teachers tab and the missed-class alert look classes up by date).
No existing row of any table is modified. Downgrade drops the index and the table (covers given would be lost).
"""
from alembic import op
import sqlalchemy as sa

revision = "a5c7e9f1b3d5"
down_revision = "f4b6c8d0e2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "class_covers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("class_session_id", sa.Uuid(), nullable=False),
        sa.Column("cover_date", sa.Date(), nullable=False),
        sa.Column("absent_employee_id", sa.Uuid(), nullable=True),
        sa.Column("substitute_employee_id", sa.Uuid(), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("amount_note", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("assigned_by", sa.Uuid(), nullable=True),
        sa.Column("cancelled_by", sa.Uuid(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["class_session_id"], ["class_sessions.id"], name=op.f("fk_class_covers_class_session_id_class_sessions"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["absent_employee_id"], ["employees.id"], name=op.f("fk_class_covers_absent_employee_id_employees"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["substitute_employee_id"], ["employees.id"], name=op.f("fk_class_covers_substitute_employee_id_employees"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"], name=op.f("fk_class_covers_assigned_by_users"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["cancelled_by"], ["users.id"], name=op.f("fk_class_covers_cancelled_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_class_covers")),
    )
    op.create_index("uq_class_covers_session_active", "class_covers", ["class_session_id"], unique=True, postgresql_where=sa.text("status = 'ACTIVE'"))
    op.create_index("ix_class_covers_substitute_date", "class_covers", ["substitute_employee_id", "cover_date"], unique=False)
    op.create_index("ix_class_covers_date", "class_covers", ["cover_date"], unique=False)
    with op.get_context().autocommit_block():
        op.create_index("ix_class_sessions_session_date", "class_sessions", ["session_date"], unique=False,
                        postgresql_concurrently=True, if_not_exists=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("ix_class_sessions_session_date", table_name="class_sessions", postgresql_concurrently=True, if_exists=True)
    op.drop_index("ix_class_covers_date", table_name="class_covers")
    op.drop_index("ix_class_covers_substitute_date", table_name="class_covers")
    op.drop_index("uq_class_covers_session_active", table_name="class_covers")
    op.drop_table("class_covers")