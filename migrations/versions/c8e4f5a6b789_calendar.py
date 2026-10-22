"""Academic calendar (holidays, exams, events, term dates, deadlines) and who may edit it.

Revision ID: c8e4f5a6b789
Revises: b7d3e4f5a678
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c8e4f5a6b789"
down_revision = "b7d3e4f5a678"
branch_labels = None
depends_on = None

PERMS = (("calendar.manage", "Add and remove holidays, exams, events and deadlines on the academic calendar", ["ADMIN"]),)


def upgrade() -> None:
    op.create_table(
        "calendar_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("for_students", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("for_staff", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("end_date >= start_date", name="ck_calendar_events_dates_in_order"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_calendar_events_created_by_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_calendar_events"),
    )
    op.create_index("ix_calendar_events_dates", "calendar_events", ["start_date", "end_date"])
    for code, description, roles in PERMS:
        op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
                           "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=description))
        for role in roles:
            op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
                               "WHERE r.code = :r AND p.code = :c AND NOT EXISTS "
                               "(SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(r=role, c=code))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'calendar.manage')")
    op.execute("DELETE FROM permissions WHERE code = 'calendar.manage'")
    op.drop_index("ix_calendar_events_dates", table_name="calendar_events")
    op.drop_table("calendar_events")