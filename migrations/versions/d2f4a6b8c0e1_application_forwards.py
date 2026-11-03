"""Applications forwarded to departments (Exams, Accounts, Library ...) and their answers.

Revision ID: d2f4a6b8c0e1
Revises: b0c1d2e3f4a5
Create Date: 2026-11-03 00:00:00.000000

Everything here is ADDITIVE and safe to run while the old version of the app is still serving:
- one new table, application_forwards, with two indexes. The old code never reads or writes it.
No existing table or row is changed: application statuses stay as they are (a forwarded application is
"Under review"); the department's stage lives only in the new table.

Downgrade drops the table (the record of which departments handled which applications is lost; the
applications themselves, their messages and decisions are not affected).
"""
from alembic import op
import sqlalchemy as sa

revision = "d2f4a6b8c0e1"
down_revision = "b0c1d2e3f4a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "application_forwards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("department", sa.String(length=30), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=15), server_default="FORWARDED", nullable=False),
        sa.Column("forwarded_by", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("responded_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], name=op.f("fk_application_forwards_application_id_applications"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["forwarded_by"], ["users.id"], name=op.f("fk_application_forwards_forwarded_by_users"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["responded_by"], ["users.id"], name=op.f("fk_application_forwards_responded_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_forwards")),
    )
    op.create_index("ix_application_forwards_application", "application_forwards", ["application_id"], unique=False)
    op.create_index("ix_application_forwards_department_status", "application_forwards", ["department", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_application_forwards_department_status", table_name="application_forwards")
    op.drop_index("ix_application_forwards_application", table_name="application_forwards")
    op.drop_table("application_forwards")