"""staff timekeeping (timings, daily in/out) + payroll attendance and security deposit + the gate becomes a visitor log book

Staff timings and staff check-in history move from the gate tables into the new ones; student gate history is removed
(as decided by the administration), then the old gate tables are dropped.

Revision ID: a06c7d8e9f01
Revises: ff5b6c7d8e9f
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a06c7d8e9f01"
down_revision = "ff5b6c7d8e9f"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
PERMS = (
    ("staff_attendance.mark", "Mark staff in and out", ["ADMIN", "ACCOUNTS_OFFICER", "SECURITY_GUARD"]),
    ("staff_attendance.view", "See staff attendance: any day, the month, timings", ["ADMIN", "ACCOUNTS_OFFICER", "PRINCIPAL"]),
    ("staff_attendance.manage", "Set staff timings, correct or excuse days, holidays, import", ["ADMIN"]),
)


def _stamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    has_gate = insp.has_table("gate_logs")

    op.create_table(
        "staff_timings",
        sa.Column("id", UUID, nullable=False),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("job_role_id", UUID, sa.ForeignKey("job_roles.id", ondelete="CASCADE", name="fk_staff_timings_job_role_id_job_roles"), nullable=True),
        sa.Column("employee_id", UUID, sa.ForeignKey("employees.id", ondelete="CASCADE", name="fk_staff_timings_employee_id_employees"), nullable=True),
        sa.Column("in_time", sa.Time(), nullable=False),
        sa.Column("out_time", sa.Time(), nullable=False),
        sa.Column("grace_in_minutes", sa.Integer(), nullable=False),
        sa.Column("grace_out_minutes", sa.Integer(), nullable=False),
        sa.Column("working_days", sa.String(20), nullable=False),
        *_stamps(),
        sa.PrimaryKeyConstraint("id", name="pk_staff_timings"),
        sa.UniqueConstraint("job_role_id", name="uq_staff_timings_job_role_id"),
        sa.UniqueConstraint("employee_id", name="uq_staff_timings_employee_id"),
    )
    op.create_table(
        "staff_attendance",
        sa.Column("id", UUID, nullable=False),
        sa.Column("employee_id", UUID, sa.ForeignKey("employees.id", ondelete="CASCADE", name="fk_staff_attendance_employee_id_employees"), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("check_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_in", sa.Time(), nullable=True),
        sa.Column("expected_out", sa.Time(), nullable=True),
        sa.Column("grace_in_minutes", sa.Integer(), nullable=False),
        sa.Column("grace_out_minutes", sa.Integer(), nullable=False),
        sa.Column("is_working_day", sa.Boolean(), nullable=False),
        sa.Column("late_minutes", sa.Integer(), nullable=False),
        sa.Column("early_minutes", sa.Integer(), nullable=False),
        sa.Column("excused", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("excuse_reason", sa.String(200), nullable=True),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("in_marked_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_staff_attendance_in_marked_by_users"), nullable=True),
        sa.Column("out_marked_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_staff_attendance_out_marked_by_users"), nullable=True),
        sa.Column("note", sa.String(300), nullable=True),
        *_stamps(),
        sa.PrimaryKeyConstraint("id", name="pk_staff_attendance"),
        sa.UniqueConstraint("employee_id", "work_date", name="uq_staff_attendance_employee_day"),
    )
    op.create_index("ix_staff_attendance_date", "staff_attendance", ["work_date"])

    op.create_table(
        "visitor_entries",
        sa.Column("id", UUID, nullable=False),
        sa.Column("serial_no", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("visitor_name", sa.String(150), nullable=False),
        sa.Column("cnic", sa.String(15), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("coming_from", sa.String(150), nullable=True),
        sa.Column("purpose", sa.String(200), nullable=False),
        sa.Column("person_to_meet", sa.String(150), nullable=True),
        sa.Column("vehicle_no", sa.String(30), nullable=True),
        sa.Column("people_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("card_no", sa.String(20), nullable=True),
        sa.Column("entry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("in_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_visitor_entries_in_by_users"), nullable=True),
        sa.Column("out_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_visitor_entries_out_by_users"), nullable=True),
        sa.Column("note", sa.String(300), nullable=True),
        *_stamps(),
        sa.PrimaryKeyConstraint("id", name="pk_visitor_entries"),
        sa.UniqueConstraint("serial_no", name="uq_visitor_entries_serial_no"),
    )
    op.create_index("ix_visitor_entries_entry_at", "visitor_entries", ["entry_at"])
    op.create_index("ix_visitor_entries_cnic", "visitor_entries", ["cnic"])

    op.create_table(
        "employee_payroll_settings",
        sa.Column("employee_id", UUID, sa.ForeignKey("employees.id", ondelete="CASCADE", name="fk_employee_payroll_settings_employee_id_employees"), nullable=False),
        sa.Column("security_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("security_opening", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("note", sa.String(200), nullable=True),
        *_stamps(),
        sa.PrimaryKeyConstraint("employee_id", name="pk_employee_payroll_settings"),
    )
    op.add_column("payroll_runs", sa.Column("working_days", sa.Integer(), nullable=False, server_default="26"))
    for name, col in (("working_days", sa.Column("working_days", sa.Integer(), nullable=False, server_default="26")),
                      ("absent_days", sa.Column("absent_days", sa.Numeric(4, 1), nullable=False, server_default="0")),
                      ("casual_leaves", sa.Column("casual_leaves", sa.Numeric(4, 1), nullable=False, server_default="0")),
                      ("late_marks", sa.Column("late_marks", sa.Integer(), nullable=False, server_default="0")),
                      ("attendance_override", sa.Column("attendance_override", sa.Boolean(), nullable=False, server_default="false")),
                      ("attendance_note", sa.Column("attendance_note", sa.String(300), nullable=True))):
        op.add_column("payslips", col)
    # Payslips made before this change divided by the days in the month: keep that, so they read the same.
    op.execute("UPDATE payslips SET working_days = days_in_month")
    op.execute("UPDATE payroll_runs SET working_days = EXTRACT(DAY FROM (date_trunc('month', month) + interval '1 month - 1 day'))::int")

    # ---- move what is worth keeping out of the gate tables
    if has_gate:
        op.execute("""
            INSERT INTO staff_timings (id, scope, in_time, out_time, grace_in_minutes, grace_out_minutes, working_days, created_at, updated_at)
            SELECT gen_random_uuid(), 'DEFAULT', COALESCE(staff_default_in, '09:00'), COALESCE(staff_default_out, '17:00'),
                   staff_grace_minutes, 0, staff_default_days, now(), now()
            FROM gate_settings WHERE id = 1""")
        op.execute("""
            INSERT INTO staff_timings (id, scope, employee_id, in_time, out_time, grace_in_minutes, grace_out_minutes, working_days, created_at, updated_at)
            SELECT gen_random_uuid(), 'EMPLOYEE', s.employee_id, s.in_time, s.out_time,
                   COALESCE((SELECT staff_grace_minutes FROM gate_settings WHERE id = 1), 10), 0, s.working_days, now(), now()
            FROM gate_employee_schedules s WHERE s.out_time > s.in_time""")
        op.execute("""
            INSERT INTO staff_attendance (id, employee_id, work_date, check_in_at, check_out_at, expected_in, expected_out, grace_in_minutes,
                                          grace_out_minutes, is_working_day, late_minutes, early_minutes, excused, source, in_marked_by, out_marked_by,
                                          note, created_at, updated_at)
            SELECT g.id, g.person_id, g.log_date, g.check_in_at, g.check_out_at, g.expected_in, g.expected_out, g.grace_minutes,
                   g.grace_minutes, g.is_working_day, g.late_minutes, g.early_minutes, false, 'GATE', g.in_marked_by, g.out_marked_by,
                   LEFT(g.note, 300), g.created_at, g.updated_at
            FROM gate_logs g JOIN employees e ON e.id = g.person_id
            WHERE g.kind = 'EMPLOYEE' AND g.check_in_at IS NOT NULL""")
    op.execute("""
        INSERT INTO staff_timings (id, scope, in_time, out_time, grace_in_minutes, grace_out_minutes, working_days, created_at, updated_at)
        SELECT gen_random_uuid(), 'DEFAULT', '09:00', '17:00', 10, 0, '0,1,2,3,4,5', now(), now()
        WHERE NOT EXISTS (SELECT 1 FROM staff_timings WHERE scope = 'DEFAULT')""")

    if has_gate:
        op.drop_index("ix_gate_logs_date", table_name="gate_logs")
        op.drop_table("gate_logs")
        op.drop_table("gate_employee_schedules")
        op.drop_table("gate_settings")

    for code, description, roles in PERMS:
        op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
                           "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=description))
        for role in roles:
            op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
                               "WHERE r.code = :r AND p.code = :c AND NOT EXISTS "
                               "(SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(r=role, c=code))
    op.execute("UPDATE permissions SET description = 'Keep the visitor log book at the gate' WHERE code = 'gate.operate'")
    op.execute("UPDATE permissions SET description = 'Correct and export the visitor log book' WHERE code = 'gate.manage'")


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code LIKE 'staff_attendance.%')")
    op.execute("DELETE FROM permissions WHERE code LIKE 'staff_attendance.%'")
    op.create_table(
        "gate_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_in_time", sa.Time(), nullable=False, server_default="08:30"),
        sa.Column("student_out_time", sa.Time(), nullable=True),
        sa.Column("student_grace_minutes", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("student_days", sa.String(20), nullable=False, server_default="0,1,2,3,4"),
        sa.Column("student_fine_per_hour", sa.Numeric(10, 2), nullable=False, server_default="100"),
        sa.Column("student_max_fine_per_day", sa.Numeric(10, 2), nullable=True),
        sa.Column("student_auto_fine", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("staff_default_in", sa.Time(), nullable=True, server_default="09:00"),
        sa.Column("staff_default_out", sa.Time(), nullable=True, server_default="17:00"),
        sa.Column("staff_grace_minutes", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("staff_default_days", sa.String(20), nullable=False, server_default="0,1,2,3,4"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("INSERT INTO gate_settings (id) VALUES (1)")
    op.create_table(
        "gate_employee_schedules",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("employee_id", UUID, sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("in_time", sa.Time(), nullable=False),
        sa.Column("out_time", sa.Time(), nullable=False),
        sa.Column("working_days", sa.String(20), nullable=False, server_default="0,1,2,3,4"),
        *_stamps(),
    )
    op.create_table(
        "gate_logs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("person_id", UUID, nullable=False),
        sa.Column("log_date", sa.Date(), nullable=False),
        sa.Column("check_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_in", sa.Time(), nullable=True),
        sa.Column("expected_out", sa.Time(), nullable=True),
        sa.Column("grace_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_working_day", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("late_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("early_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("in_marked_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("out_marked_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("fine_id", UUID, sa.ForeignKey("fines.id", ondelete="SET NULL"), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("kind", "person_id", "log_date", name="uq_gate_logs_person_day"),
    )
    op.create_index("ix_gate_logs_date", "gate_logs", ["log_date"])
    op.execute("""
        INSERT INTO gate_logs (id, kind, person_id, log_date, check_in_at, check_out_at, expected_in, expected_out, grace_minutes, is_working_day,
                               late_minutes, early_minutes, in_marked_by, out_marked_by, note, created_at, updated_at)
        SELECT id, 'EMPLOYEE', employee_id, work_date, check_in_at, check_out_at, expected_in, expected_out, grace_in_minutes, is_working_day,
               late_minutes, early_minutes, in_marked_by, out_marked_by, note, created_at, updated_at FROM staff_attendance""")
    op.execute("""
        INSERT INTO gate_employee_schedules (id, employee_id, in_time, out_time, working_days, created_at, updated_at)
        SELECT gen_random_uuid(), employee_id, in_time, out_time, working_days, now(), now() FROM staff_timings WHERE scope = 'EMPLOYEE'""")
    for col in ("attendance_note", "attendance_override", "late_marks", "casual_leaves", "absent_days", "working_days"):
        op.drop_column("payslips", col)
    op.drop_column("payroll_runs", "working_days")
    op.drop_table("employee_payroll_settings")
    op.drop_table("visitor_entries")
    op.drop_table("staff_attendance")
    op.drop_table("staff_timings")