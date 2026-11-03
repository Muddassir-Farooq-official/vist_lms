"""Daily to-do lists for staff (Tasks -> My to-do; administration: To-do reports).

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-11-03 00:00:00.000000

Everything here is ADDITIVE and safe to run while the old version of the app is still serving:
- one new table, todo_items, with two indexes. The old code never reads or writes it.
No existing table or row is changed. Uses the existing task.use / task.manage permissions (no permission changes).

Downgrade drops the table (every to-do list written so far is lost; nothing else is affected).
"""
from alembic import op
import sqlalchemy as sa

revision = "a9b0c1d2e3f4"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "todo_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=15), server_default="TODO", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("carried_from_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_todo_items_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["carried_from_id"], ["todo_items.id"], name=op.f("fk_todo_items_carried_from_id_todo_items"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_todo_items")),
    )
    op.create_index("ix_todo_items_user_date", "todo_items", ["user_id", "work_date"], unique=False)
    op.create_index("ix_todo_items_date", "todo_items", ["work_date"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_todo_items_date", table_name="todo_items")
    op.drop_index("ix_todo_items_user_date", table_name="todo_items")
    op.drop_table("todo_items")