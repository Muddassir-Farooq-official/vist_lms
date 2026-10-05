"""complaints module — scalable feedback & complaint tracking

Revision ID: b7d2f4a83e9c
Revises: a3c6e9f21b4d
Create Date: 2026-08-28 00:00:00.000000

New standalone `complaints` table. Complainant and target are both
polymorphic (STUDENT or EMPLOYEE) — who is ALLOWED to file one is
controlled entirely by the complaint.submit permission grant in
app/auth/permissions.py, not by this schema, so extending it to more
roles later needs no migration.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7d2f4a83e9c'
down_revision = 'a3c6e9f21b4d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'complaints',
        sa.Column('complainant_type', sa.Enum('STUDENT', 'EMPLOYEE', name='complaint_party_type'), nullable=False),
        sa.Column('complainant_id', sa.Uuid(), nullable=False),
        sa.Column('target_type', sa.Enum('STUDENT', 'EMPLOYEE', name='complaint_party_type', create_type=False), nullable=False),
        sa.Column('target_id', sa.Uuid(), nullable=False),
        sa.Column(
            'reason',
            sa.Enum(
                'HARASSMENT', 'MISBEHAVIOR', 'DISCRIMINATION', 'NEGLIGENCE', 'ACADEMIC_DISPUTE',
                'FACILITY_ISSUE', 'FINANCIAL_DISPUTE', 'OTHER', name='complaint_reason',
            ),
            nullable=False,
        ),
        sa.Column('custom_reason', sa.String(length=255), nullable=True),
        sa.Column('description', sa.String(length=2000), nullable=False),
        sa.Column(
            'status',
            sa.Enum('SUBMITTED', 'UNDER_REVIEW', 'RESOLVED', 'DISMISSED', 'WITHDRAWN', name='complaint_status'),
            nullable=False,
        ),
        sa.Column('resolved_by', sa.Uuid(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_note', sa.String(length=500), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], name=op.f('fk_complaints_resolved_by_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_complaints')),
    )
    op.create_index('ix_complaints_complainant', 'complaints', ['complainant_type', 'complainant_id'], unique=False)
    op.create_index('ix_complaints_target', 'complaints', ['target_type', 'target_id'], unique=False)
    op.create_index('ix_complaints_status', 'complaints', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_complaints_status', table_name='complaints')
    op.drop_index('ix_complaints_target', table_name='complaints')
    op.drop_index('ix_complaints_complainant', table_name='complaints')
    op.drop_table('complaints')
    op.execute("DROP TYPE IF EXISTS complaint_status")
    op.execute("DROP TYPE IF EXISTS complaint_reason")
    op.execute("DROP TYPE IF EXISTS complaint_party_type")