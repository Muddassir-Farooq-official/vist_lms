"""Certificates students ask for; the months whose report was sent to the principal.

Revision ID: e6a8c0d2f4b6
Revises: d5f7b9c1e3a5
Create Date: 2026-11-09 11:55:54.055951

ADDITIVE and safe to run while the old version of the app is still serving: two new, empty tables, nothing existing is
changed or locked for long.
* certificate_requests: a student's request, the office's decision, and links to the fee line, challan and issued
  certificate (app/records/requests.py). Rows are never deleted; the fee itself is an ordinary fee-ledger line.
* monthly_report_runs: one row per month whose report notice went out, so it is sent once (app/reports/monthly.py).
Downgrade drops both tables (the request history and the sent-report record). Fee charges already made for certificates
stay in the fee ledger either way, and the certificates themselves stay in issued_documents.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'e6a8c0d2f4b6'
down_revision = 'd5f7b9c1e3a5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('monthly_report_runs',
    sa.Column('month', sa.Date(), nullable=False),
    sa.Column('sent_to', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_monthly_report_runs')),
    sa.UniqueConstraint('month', name=op.f('uq_monthly_report_runs_month'))
    )
    op.create_table('certificate_requests',
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('purpose', sa.String(length=300), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('fee_amount', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('fee_ledger_entry_id', sa.Uuid(), nullable=True),
    sa.Column('challan_id', sa.Uuid(), nullable=True),
    sa.Column('issued_document_id', sa.Uuid(), nullable=True),
    sa.Column('override_reason', sa.Text(), nullable=True),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('requested_by', sa.Uuid(), nullable=True),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_note', sa.String(length=300), nullable=True),
    sa.Column('ready_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['challan_id'], ['payment_challans.id'], name=op.f('fk_certificate_requests_challan_id_payment_challans'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_certificate_requests_decided_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['fee_ledger_entry_id'], ['fee_ledger_entries.id'], name=op.f('fk_certificate_requests_fee_ledger_entry_id_fee_ledger_entries'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['issued_document_id'], ['issued_documents.id'], name=op.f('fk_certificate_requests_issued_document_id_issued_documents'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['requested_by'], ['users.id'], name=op.f('fk_certificate_requests_requested_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_certificate_requests_student_id_students'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_certificate_requests'))
    )
    op.create_index('ix_certificate_requests_status', 'certificate_requests', ['status'], unique=False)
    op.create_index('ix_certificate_requests_student', 'certificate_requests', ['student_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_certificate_requests_student', table_name='certificate_requests')
    op.drop_index('ix_certificate_requests_status', table_name='certificate_requests')
    op.drop_table('certificate_requests')
    op.drop_table('monthly_report_runs')
