"""Financial rows can no longer disappear through a cascading delete (audit DB-03).

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-25 00:00:00.000000

Nine foreign keys change from ON DELETE CASCADE to ON DELETE RESTRICT: deleting a fee structure, a fee, a payment,
a fine or an employee used to delete the charges, payments, challans, refunds, fine-payment reversals, salary advances
or payslips under it without a trace. Now the database refuses; the application removes only what it has checked
is unused (undoing a fee activation with no payments).

Production impact: each constraint is dropped and re-added in one short transaction. The new constraint is added NOT
VALID (no table scan while locked) and then validated, which reads the table without blocking writes. No row changes.
Run outside fee-collection hours all the same. Downgrade restores CASCADE.
"""
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None

# (table, column, referenced table)
KEYS = [
    ("fee_assignments", "fee_structure_id", "fee_structures"),
    ("fee_ledger_entries", "fee_assignment_id", "fee_assignments"),
    ("fee_ledger_entries", "related_entry_id", "fee_ledger_entries"),
    ("payment_challans", "fee_ledger_entry_id", "fee_ledger_entries"),
    ("payment_challans", "fee_assignment_id", "fee_assignments"),
    ("fee_refunds", "fee_assignment_id", "fee_assignments"),
    ("fine_payment_reversals", "fine_id", "fines"),
    ("salary_advances", "employee_id", "employees"),
    ("payslips", "employee_id", "employees"),
]


def _swap(rule: str) -> None:
    for table, column, ref in KEYS:
        name = f"fk_{table}_{column}_{ref}"
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}')
        op.execute(f'ALTER TABLE {table} ADD CONSTRAINT {name} FOREIGN KEY ({column}) REFERENCES {ref} (id) ON DELETE {rule} NOT VALID')
    for table, column, ref in KEYS:
        op.execute(f'ALTER TABLE {table} VALIDATE CONSTRAINT fk_{table}_{column}_{ref}')


def upgrade() -> None:
    _swap("RESTRICT")


def downgrade() -> None:
    _swap("CASCADE")
