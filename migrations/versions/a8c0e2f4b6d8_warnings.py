"""Warnings to students and staff.

Revision ID: a8c0e2f4b6d8
Revises: f7b9d1e3a5c7
Create Date: 2026-11-10 11:26:25.082955

ADDITIVE and safe to run while the old version of the app is still serving: two new, empty tables; nothing existing changes.
* violation_types: the dropdown of violations (filled with a starting list the first time the Warnings page is opened).
* disciplinary_warnings: each warning, the person's acknowledgement and reply, a withdrawal, and the decision on a final
  warning (with links to the fine or suspension it caused). Rows are never deleted; students/employees with warnings cannot
  be deleted (RESTRICT), which keeps the record.
Also grants warning.issue (ADMIN) and warning.decide (ADMIN, PRINCIPAL), only where missing.
Downgrade removes those two permissions and drops both tables and every warning in them. Status changes, fines and sign-in blocks already applied by decisions
stay where they are (student/employee status history, fines), so take a backup before downgrading on a live server.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8c0e2f4b6d8'
down_revision = 'f7b9d1e3a5c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('violation_types',
    sa.Column('applies_to', sa.String(length=10), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_violation_types')),
    sa.UniqueConstraint('applies_to', 'name', name='uq_violation_types_applies_to_name')
    )
    op.create_table('disciplinary_warnings',
    sa.Column('serial', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=True),
    sa.Column('employee_id', sa.Uuid(), nullable=True),
    sa.Column('violation_type_id', sa.Uuid(), nullable=True),
    sa.Column('violation', sa.String(length=120), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('incident_date', sa.Date(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('is_final', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('issued_by', sa.Uuid(), nullable=True),
    sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('response', sa.Text(), nullable=True),
    sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('withdrawn_by', sa.Uuid(), nullable=True),
    sa.Column('withdrawn_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('withdraw_reason', sa.String(length=300), nullable=True),
    sa.Column('action', sa.String(length=12), nullable=True),
    sa.Column('action_note', sa.String(length=500), nullable=True),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('fine_id', sa.Uuid(), nullable=True),
    sa.Column('fine_amount', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('lifted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('lifted_by', sa.Uuid(), nullable=True),
    sa.Column('lift_note', sa.String(length=300), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('(student_id IS NULL) <> (employee_id IS NULL)', name=op.f('ck_disciplinary_warnings_one_person')),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_disciplinary_warnings_decided_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_disciplinary_warnings_employee_id_employees'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['fine_id'], ['fines.id'], name=op.f('fk_disciplinary_warnings_fine_id_fines'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['issued_by'], ['users.id'], name=op.f('fk_disciplinary_warnings_issued_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['lifted_by'], ['users.id'], name=op.f('fk_disciplinary_warnings_lifted_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_disciplinary_warnings_student_id_students'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['violation_type_id'], ['violation_types.id'], name=op.f('fk_disciplinary_warnings_violation_type_id_violation_types'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['withdrawn_by'], ['users.id'], name=op.f('fk_disciplinary_warnings_withdrawn_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_disciplinary_warnings')),
    sa.UniqueConstraint('serial', name=op.f('uq_disciplinary_warnings_serial'))
    )
    op.create_index('ix_disciplinary_warnings_action', 'disciplinary_warnings', ['action'], unique=False)
    op.create_index('ix_disciplinary_warnings_employee', 'disciplinary_warnings', ['employee_id'], unique=False)
    op.create_index('ix_disciplinary_warnings_student', 'disciplinary_warnings', ['student_id'], unique=False)

    # the two permissions (deploy also runs scripts/seed_permissions.py; this keeps it right even without that step)
    for code, desc, roles in (("warning.issue", "Issue warnings to students and staff", ("ADMIN",)),
                              ("warning.decide", "See all warnings and decide the action on a final one", ("ADMIN", "PRINCIPAL"))):
        op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
                           "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=desc))
        for role in roles:
            op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
                               "WHERE r.code = :r AND p.code = :c AND NOT EXISTS "
                               "(SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(r=role, c=code))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('warning.issue', 'warning.decide'))")
    op.execute("DELETE FROM permissions WHERE code IN ('warning.issue', 'warning.decide')")
    op.drop_index('ix_disciplinary_warnings_student', table_name='disciplinary_warnings')
    op.drop_index('ix_disciplinary_warnings_employee', table_name='disciplinary_warnings')
    op.drop_index('ix_disciplinary_warnings_action', table_name='disciplinary_warnings')
    op.drop_table('disciplinary_warnings')
    op.drop_table('violation_types')
