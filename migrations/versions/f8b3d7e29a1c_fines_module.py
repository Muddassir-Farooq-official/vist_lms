"""fines module — scalable, cross-role fine tracking for students/employees

Revision ID: f8b3d7e29a1c
Revises: e5a9c1f04b3d
Create Date: 2026-08-28 00:00:00.000000

New standalone `fines` table. Deliberately not part of the Fees module
— see app/fines/models.py for the reasoning (ad-hoc/reason-driven vs
structured installment plans). Also grants the new fine.issue,
fine.view, fine.manage permissions are handled by scripts/seed_permissions.py
(additive, run after this migration), not by this migration itself —
consistent with how every other permission in this codebase is seeded.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f8b3d7e29a1c'
down_revision = 'e5a9c1f04b3d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'fines',
        sa.Column('payer_type', sa.Enum('STUDENT', 'EMPLOYEE', name='fine_payer_type'), nullable=False),
        sa.Column('payer_id', sa.Uuid(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('reason', sa.String(length=500), nullable=False),
        sa.Column('source', sa.Enum('LIBRARY', 'DISCIPLINARY', 'OTHER', name='fine_source'), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'PAID', 'WAIVED', name='fine_status'), nullable=False),
        sa.Column('issued_by', sa.Uuid(), nullable=True),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_by', sa.Uuid(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_note', sa.String(length=500), nullable=True),
        sa.Column('related_entity_type', sa.String(length=50), nullable=True),
        sa.Column('related_entity_id', sa.Uuid(), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['issued_by'], ['users.id'], name=op.f('fk_fines_issued_by_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], name=op.f('fk_fines_resolved_by_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_fines')),
    )
    op.create_index('ix_fines_payer', 'fines', ['payer_type', 'payer_id'], unique=False)
    op.create_index('ix_fines_status', 'fines', ['status'], unique=False)
    op.create_index('ix_fines_source', 'fines', ['source'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_fines_source', table_name='fines')
    op.drop_index('ix_fines_status', table_name='fines')
    op.drop_index('ix_fines_payer', table_name='fines')
    op.drop_table('fines')
    op.execute("DROP TYPE IF EXISTS fine_status")
    op.execute("DROP TYPE IF EXISTS fine_source")
    op.execute("DROP TYPE IF EXISTS fine_payer_type")