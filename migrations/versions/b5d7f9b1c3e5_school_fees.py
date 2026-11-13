"""School mode, stage 4: school fees (class fee plans, transport routes, monthly and admission vouchers).

Production impact: additive, safe to run on the live database.
  - four new, empty tables: school_fee_plans, school_transport_routes, school_student_transport and school_vouchers,
    and one new enum type (school_voucher_kind);
  - one new column fee_structures.late_fee_once, boolean NOT NULL DEFAULT false. On PostgreSQL 11+ adding a column
    with a constant default does not rewrite the table, and every existing fee structure gets false, so every
    existing fee keeps its daily late fee exactly as before.
  - no existing row is changed. The tables are used only when School mode is on.
Downgrade drops the four tables and the enum type, and the column. Before dropping the column it clears the late fee
of the structures that used a fixed fine (late_fee_once = true), so a fixed Rs. 200 fine never turns into Rs. 200 a
day; the fee ledger rows, challans and payments made by school vouchers stay (only the line-by-line voucher details
in school_vouchers are lost).

Revision ID: b5d7f9b1c3e5
Revises: a4c6e8a0b2d4
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b5d7f9b1c3e5'
down_revision = 'a4c6e8a0b2d4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('school_transport_routes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('monthly_fee', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_school_transport_routes')),
    sa.UniqueConstraint('name', name=op.f('uq_school_transport_routes_name'))
    )
    op.create_table('school_fee_plans',
    sa.Column('term_id', sa.Uuid(), nullable=False),
    sa.Column('program_id', sa.Uuid(), nullable=False),
    sa.Column('monthly_tuition', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('admission_fee', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('annual_charges', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['program_id'], ['programs.id'], name=op.f('fk_school_fee_plans_program_id_programs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['term_id'], ['academic_terms.id'], name=op.f('fk_school_fee_plans_term_id_academic_terms'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_school_fee_plans_updated_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('term_id', 'program_id', name=op.f('pk_school_fee_plans'))
    )
    op.create_table('school_student_transport',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('route_id', sa.Uuid(), nullable=False),
    sa.Column('from_month', sa.Date(), nullable=False),
    sa.Column('until_month', sa.Date(), nullable=True),
    sa.Column('added_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['added_by'], ['users.id'], name=op.f('fk_school_student_transport_added_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['route_id'], ['school_transport_routes.id'], name=op.f('fk_school_student_transport_route_id_school_transport_routes'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_school_student_transport_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_school_student_transport'))
    )
    op.create_index('ix_school_student_transport_student', 'school_student_transport', ['student_id', 'from_month'], unique=False)
    op.create_table('school_vouchers',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.Enum('MONTHLY', 'ADMISSION', name='school_voucher_kind'), nullable=False),
    sa.Column('month', sa.Date(), nullable=False),
    sa.Column('term_id', sa.Uuid(), nullable=False),
    sa.Column('program_id', sa.Uuid(), nullable=False),
    sa.Column('tuition', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('transport', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('route_name', sa.String(length=80), nullable=True),
    sa.Column('annual', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('admission', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('sibling_discount', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('sibling_rank', sa.Integer(), nullable=True),
    sa.Column('ledger_entry_id', sa.Uuid(), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_school_vouchers_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['ledger_entry_id'], ['fee_ledger_entries.id'], name=op.f('fk_school_vouchers_ledger_entry_id_fee_ledger_entries'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['program_id'], ['programs.id'], name=op.f('fk_school_vouchers_program_id_programs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_school_vouchers_student_id_students'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['term_id'], ['academic_terms.id'], name=op.f('fk_school_vouchers_term_id_academic_terms'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_school_vouchers')),
    sa.UniqueConstraint('student_id', 'kind', 'month', name='uq_school_vouchers_student_kind_month')
    )
    op.create_index(op.f('ix_school_vouchers_student_id'), 'school_vouchers', ['student_id'], unique=False)
    op.add_column('fee_structures', sa.Column('late_fee_once', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.execute("UPDATE fee_structures SET late_fee_per_day = NULL WHERE late_fee_once")   # a fixed fine must not become a daily one
    op.drop_column('fee_structures', 'late_fee_once')
    op.drop_index(op.f('ix_school_vouchers_student_id'), table_name='school_vouchers')
    op.drop_table('school_vouchers')
    op.drop_index('ix_school_student_transport_student', table_name='school_student_transport')
    op.drop_table('school_student_transport')
    op.drop_table('school_fee_plans')
    op.drop_table('school_transport_routes')
    op.execute("DROP TYPE IF EXISTS school_voucher_kind")
