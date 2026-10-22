"""People controls: leave that excuses attendance, leaver's last day, Advance Semester guard,
and history that can never be deleted by accident.

Revision ID: a6c2d3e4f567
Revises: f5b1c2d3e456
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a6c2d3e4f567"
down_revision = "f5b1c2d3e456"
branch_labels = None
depends_on = None

# Rows here are history: nobody deletes them (a mistake is reversed, a person is closed, never erased).
# For a real maintenance job: SET LOCAL vist.allow_delete = 'on'; inside that transaction.
PROTECTED = ("students", "employees", "users", "enrollments", "enrollment_results", "audit_logs", "payroll_runs", "fines")


def upgrade() -> None:
    op.add_column("application_types", sa.Column("excuses_attendance", sa.Boolean(), nullable=False, server_default="false"))
    op.execute("UPDATE application_types SET excuses_attendance = true WHERE requires_dates AND name IN ('Leave application', 'Sick leave')")
    op.add_column("employees", sa.Column("left_on", sa.Date(), nullable=True))
    op.execute("""UPDATE employees e SET left_on = COALESCE(
                    (SELECT max(a.created_at)::date FROM audit_logs a WHERE a.entity_id = e.id AND a.action LIKE 'employee.status%'),
                    e.updated_at::date)
                  WHERE e.status = 'TERMINATED'""")
    op.add_column("students", sa.Column("semester_advanced_on", sa.Date(), nullable=True))

    op.execute("""
        CREATE OR REPLACE FUNCTION vist_keep_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF coalesce(current_setting('vist.allow_delete', true), '') = 'on' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'Rows in % are history and cannot be deleted (close or reverse them instead).', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END $$;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION vist_keep_money_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            -- A charge (DEBIT) may go when its fee activation is undone; money received, discounts and
            -- refunds are history.
            IF OLD.entry_type = 'DEBIT' OR coalesce(current_setting('vist.allow_delete', true), '') = 'on' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'Payments, discounts and refunds cannot be deleted; reverse them instead.'
                USING ERRCODE = 'restrict_violation';
        END $$;
    """)
    for t in PROTECTED:
        op.execute(f"CREATE TRIGGER trg_{t}_keep_history BEFORE DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION vist_keep_history()")
    op.execute("CREATE TRIGGER trg_fee_ledger_entries_keep_history BEFORE DELETE ON fee_ledger_entries "
               "FOR EACH ROW EXECUTE FUNCTION vist_keep_money_history()")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_fee_ledger_entries_keep_history ON fee_ledger_entries")
    for t in PROTECTED:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{t}_keep_history ON {t}")
    op.execute("DROP FUNCTION IF EXISTS vist_keep_money_history()")
    op.execute("DROP FUNCTION IF EXISTS vist_keep_history()")
    op.drop_column("students", "semester_advanced_on")
    op.drop_column("employees", "left_on")
    op.drop_column("application_types", "excuses_attendance")