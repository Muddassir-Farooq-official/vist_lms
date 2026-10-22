"""Hostel: rooms, stays, the monthly charge (added to dues as a HOSTEL charge), and the warden's access.

Revision ID: e1a6b7c8d901
Revises: d9f5a6b7c890
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e1a6b7c8d901"
down_revision = "d9f5a6b7c890"
branch_labels = None
depends_on = None

PERMS = (
    ("hostel.view", "See hostel rooms, residents and charges", ["ADMIN", "HOSTEL_WARDEN", "PRINCIPAL"]),
    ("hostel.manage", "Rooms, check in / out, moves and the monthly hostel charge", ["ADMIN", "HOSTEL_WARDEN"]),
    ("student.view", None, ["HOSTEL_WARDEN"]),          # the warden looks students up to give them a room
)


def _stamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.execute("ALTER TYPE fine_source ADD VALUE IF NOT EXISTS 'HOSTEL'")
    op.create_table(
        "hostel_rooms",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("block", sa.String(60), nullable=True),
        sa.Column("floor", sa.String(20), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("gender", sa.String(10), nullable=False),
        sa.Column("monthly_fee", sa.Numeric(10, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("note", sa.String(200), nullable=True),
        *_stamps(),
        sa.CheckConstraint("capacity > 0", name="ck_hostel_rooms_capacity_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_hostel_rooms"),
        sa.UniqueConstraint("code", name="uq_hostel_rooms_code"),
    )
    op.create_table(
        "hostel_stays",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("bed_no", sa.String(10), nullable=True),
        sa.Column("monthly_fee", sa.Numeric(10, 2), nullable=False),
        sa.Column("end_reason", sa.String(200), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        *_stamps(),
        sa.ForeignKeyConstraint(["room_id"], ["hostel_rooms.id"], name="fk_hostel_stays_room_id_hostel_rooms", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], name="fk_hostel_stays_student_id_students", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_hostel_stays_created_by_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_hostel_stays"),
    )
    op.create_index("ix_hostel_stays_room", "hostel_stays", ["room_id"])
    op.create_index("ix_hostel_stays_student", "hostel_stays", ["student_id"])
    op.create_index("uq_hostel_stays_one_current_per_student", "hostel_stays", ["student_id"], unique=True, postgresql_where=sa.text("end_date IS NULL"))
    op.create_table(
        "hostel_charges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("stay_id", sa.Uuid(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("fine_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        *_stamps(),
        sa.ForeignKeyConstraint(["stay_id"], ["hostel_stays.id"], name="fk_hostel_charges_stay_id_hostel_stays", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["fine_id"], ["fines.id"], name="fk_hostel_charges_fine_id_fines", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_hostel_charges_created_by_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_hostel_charges"),
        sa.UniqueConstraint("stay_id", "month", name="uq_hostel_charges_stay_month"),
    )
    for t in ("hostel_stays", "hostel_charges"):
        op.execute(f"CREATE TRIGGER trg_{t}_keep_history BEFORE DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION vist_keep_history()")
    for code, description, roles in PERMS:
        if description:
            op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
                               "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=description))
        for role in roles:
            op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
                               "WHERE r.code = :r AND p.code = :c AND NOT EXISTS "
                               "(SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(r=role, c=code))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('hostel.view', 'hostel.manage'))")
    op.execute("DELETE FROM permissions WHERE code IN ('hostel.view', 'hostel.manage')")
    op.execute("DELETE FROM role_permissions rp USING roles r, permissions p WHERE rp.role_id = r.id AND rp.permission_id = p.id "
               "AND r.code = 'HOSTEL_WARDEN' AND p.code = 'student.view'")
    for t in ("hostel_stays", "hostel_charges"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{t}_keep_history ON {t}")
    op.drop_table("hostel_charges")
    op.drop_index("uq_hostel_stays_one_current_per_student", table_name="hostel_stays")
    op.drop_index("ix_hostel_stays_student", table_name="hostel_stays")
    op.drop_index("ix_hostel_stays_room", table_name="hostel_stays")
    op.drop_table("hostel_stays")
    op.drop_table("hostel_rooms")
    # PostgreSQL cannot drop an enum value; HOSTEL stays in fine_source (unused).