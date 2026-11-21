"""University Reg No deadlines and admit-card exemptions.

Production impact: one new nullable column academic_terms.reg_deadline (empty for every existing term, so nothing is
reminded or held until someone sets a deadline) and one new empty table admit_card_exemptions. No existing row changes;
adding a nullable column without a default is instant in PostgreSQL.

Revision ID: b6d8f0a2c4e7
Revises: a5c7e9b1d3f6
Create Date: 2026-11-21 20:47:12.586161

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b6d8f0a2c4e7'
down_revision = 'a5c7e9b1d3f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('admit_card_exemptions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('until', sa.Date(), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revoked_by', sa.Uuid(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_admit_card_exemptions_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['revoked_by'], ['users.id'], name=op.f('fk_admit_card_exemptions_revoked_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_admit_card_exemptions_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_admit_card_exemptions'))
    )
    op.create_index(op.f('ix_admit_card_exemptions_student_id'), 'admit_card_exemptions', ['student_id'], unique=False)
    op.add_column('academic_terms', sa.Column('reg_deadline', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('academic_terms', 'reg_deadline')
    op.drop_index(op.f('ix_admit_card_exemptions_student_id'), table_name='admit_card_exemptions')
    op.drop_table('admit_card_exemptions')
