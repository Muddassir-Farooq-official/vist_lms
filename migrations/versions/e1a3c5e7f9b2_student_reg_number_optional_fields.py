"""Students: university registration number (Reg No), and personal / guardian / address details that the institute
may make optional (Students -> Field rules).

Production impact:
  * adds students.reg_number (NULL) with a case-insensitive unique index (only filled values are compared);
  * lets these columns be empty: students.date_of_birth, gender, cnic, contact_no, marital_status, religion,
    nationality; student_guardians.name, cnic, relation; student_addresses.province, district, city.
    Nothing is changed in existing rows; all existing students keep every value they have. The CNIC format checks
    and the unique CNIC constraint stay (they ignore empty values).
  * The field rules themselves are rows in the existing policy_settings table (no new table).
Downgrade drops reg_number and makes the columns NOT NULL again; it stops with a message (changing nothing) if a
student was saved without one of those details since the upgrade.

Revision ID: e1a3c5e7f9b2
Revises: d4e6f8a0b2c4
"""
from alembic import op
import sqlalchemy as sa


revision = 'e1a3c5e7f9b2'
down_revision = 'd4e6f8a0b2c4'
branch_labels = None
depends_on = None

OPTIONAL = {
    'students': ('date_of_birth', 'gender', 'cnic', 'contact_no', 'marital_status', 'religion', 'nationality'),
    'student_guardians': ('name', 'cnic', 'relation'),
    'student_addresses': ('province', 'district', 'city'),
}


def upgrade() -> None:
    op.add_column('students', sa.Column('reg_number', sa.String(length=40), nullable=True))
    op.create_index('uq_students_reg_number_ci', 'students', [sa.text('upper(reg_number)')], unique=True)
    for table, cols in OPTIONAL.items():
        for col in cols:
            op.alter_column(table, col, nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table, cols in OPTIONAL.items():
        cond = " OR ".join(f"{c} IS NULL" for c in cols)
        n = bind.execute(sa.text(f"SELECT count(*) FROM {table} WHERE {cond}")).scalar()
        if n:
            raise RuntimeError(f"Cannot downgrade: {n} row(s) in {table} have an empty {', '.join(cols)}. Fill them in first.")
    for table, cols in OPTIONAL.items():
        for col in cols:
            op.alter_column(table, col, nullable=False)
    op.drop_index('uq_students_reg_number_ci', table_name='students')
    op.drop_column('students', 'reg_number')
