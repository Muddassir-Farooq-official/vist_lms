"""Staff leave applications: a member of staff applies, an administrator approves or refuses.

Revision ID: d8f0a2b4c6e8
Revises: c7e9f1a3b5d7
Create Date: 2026-11-07 00:00:02.000000

ADDITIVE and safe to run while the old version of the app is still serving:
- new, empty table leave_requests (nothing existing is copied or changed).
Payslips already paid are never changed. Downgrade drops the table (the applications would be lost; days already
excused in staff attendance stay excused).
"""
from alembic import op
import sqlalchemy as sa

revision = "d8f0a2b4c6e8"
down_revision = "c7e9f1a3b5d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leave_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("first_day", sa.Date(), nullable=False),
        sa.Column("last_day", sa.Date(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], name=op.f("fk_leave_requests_employee_id_employees"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], name=op.f("fk_leave_requests_requested_by_users"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], name=op.f("fk_leave_requests_decided_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leave_requests")),
    )
    op.create_index("ix_leave_requests_employee", "leave_requests", ["employee_id"], unique=False)
    op.create_index("ix_leave_requests_status", "leave_requests", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_leave_requests_status", table_name="leave_requests")
    op.drop_index("ix_leave_requests_employee", table_name="leave_requests")
    op.drop_table("leave_requests")
