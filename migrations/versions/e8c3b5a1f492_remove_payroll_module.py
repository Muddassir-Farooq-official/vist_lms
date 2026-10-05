"""remove payroll module

Revision ID: e8c3b5a1f492
Revises: d4a7f2c93b8e
Create Date: 2026-09-29 00:00:00.000000

Payroll (periods, deductions, generated entries, mark-paid) is being
taken out of the product for now and will return later as its own
piece of work -- per explicit request in conversation. Salary history
and bonuses are employee compensation records, NOT payroll runs, and
stay exactly as they are; only the link from a bonus to a payroll
period goes away.

This drops payroll_entries, payroll_deductions, payroll_periods, the
payroll_period_status enum, employee_bonuses.payroll_period_id, and
the two payroll.* permission rows (role_permissions rows cascade with
them). Any payroll data that exists is deleted -- deliberately not
reversible: the downgrade rebuilds the EMPTY schema only, so it can be
rolled back structurally but never restores rows.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'e8c3b5a1f492'
down_revision = 'd4a7f2c93b8e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        'fk_employee_bonuses_payroll_period_id_payroll_periods', 'employee_bonuses', type_='foreignkey',
    )
    op.drop_column('employee_bonuses', 'payroll_period_id')

    # Children before the parent table they reference.
    op.drop_table('payroll_entries')
    op.drop_table('payroll_deductions')
    op.drop_table('payroll_periods')
    op.execute("DROP TYPE IF EXISTS payroll_period_status")

    # role_permissions rows for these cascade automatically.
    op.execute("DELETE FROM permissions WHERE code IN ('payroll.manage', 'payroll.view')")


def downgrade() -> None:
    op.execute("CREATE TYPE payroll_period_status AS ENUM ('OPEN', 'GENERATED', 'PAID')")

    op.create_table(
        'payroll_periods',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('period_label', sa.String(length=20), nullable=False),
        sa.Column(
            'status', postgresql.ENUM('OPEN', 'GENERATED', 'PAID', name='payroll_period_status', create_type=False),
            nullable=False,
        ),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('generated_by', sa.Uuid(), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['generated_by'], ['users.id'], name=op.f('fk_payroll_periods_generated_by_users'), ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_payroll_periods')),
        sa.UniqueConstraint('period_label', name='uq_payroll_periods_label'),
    )

    op.create_table(
        'payroll_deductions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('payroll_period_id', sa.Uuid(), nullable=False),
        sa.Column('employee_id', sa.Uuid(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('reason', sa.String(length=255), nullable=False),
        sa.Column('added_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.Date(), nullable=False),
        sa.CheckConstraint('amount >= 0', name=op.f('ck_payroll_deductions_non_negative_amount')),
        sa.ForeignKeyConstraint(
            ['payroll_period_id'], ['payroll_periods.id'],
            name=op.f('fk_payroll_deductions_payroll_period_id_payroll_periods'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['employee_id'], ['employees.id'],
            name=op.f('fk_payroll_deductions_employee_id_employees'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['added_by'], ['users.id'], name=op.f('fk_payroll_deductions_added_by_users'), ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_payroll_deductions')),
    )
    op.create_index(
        'ix_payroll_deductions_period_employee', 'payroll_deductions', ['payroll_period_id', 'employee_id'],
        unique=False,
    )

    op.create_table(
        'payroll_entries',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('payroll_period_id', sa.Uuid(), nullable=False),
        sa.Column('employee_id', sa.Uuid(), nullable=False),
        sa.Column('base_salary', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('bonuses_total', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('deductions_total', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('net_pay', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['payroll_period_id'], ['payroll_periods.id'],
            name=op.f('fk_payroll_entries_payroll_period_id_payroll_periods'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['employee_id'], ['employees.id'],
            name=op.f('fk_payroll_entries_employee_id_employees'), ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_payroll_entries')),
        sa.UniqueConstraint('payroll_period_id', 'employee_id', name='uq_payroll_entries_period_employee'),
    )
    op.create_index('ix_payroll_entries_employee_id', 'payroll_entries', ['employee_id'], unique=False)

    op.add_column('employee_bonuses', sa.Column('payroll_period_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        'fk_employee_bonuses_payroll_period_id_payroll_periods', 'employee_bonuses', 'payroll_periods',
        ['payroll_period_id'], ['id'], ondelete='SET NULL',
    )
    # The deleted payroll.* permission rows are not recreated here --
    # scripts/seed_permissions.py restores them from code if/when the
    # payroll module returns and re-declares them.