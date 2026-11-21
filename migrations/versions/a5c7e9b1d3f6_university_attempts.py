"""University result attempts (affiliated programs): every imported university result is kept, the first exam and
each supplementary re-sit.

Production impact: one NEW table (university_result_attempts), empty. Results already imported keep working as they
are (enrollment_results is not changed); their first attempt is recorded the first time a supplementary result is
imported for that subject. Downgrade drops the table (only the attempt history is lost; the result that counts stays).

Revision ID: a5c7e9b1d3f6
Revises: f3b5d7f9a1c4
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a5c7e9b1d3f6'
down_revision = 'f3b5d7f9a1c4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('university_result_attempts',
    sa.Column('enrollment_id', sa.Uuid(), nullable=False),
    sa.Column('attempt_no', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=14), nullable=False),
    sa.Column('percentage', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('total_obtained', sa.Numeric(precision=8, scale=2), nullable=False),
    sa.Column('total_max', sa.Numeric(precision=8, scale=2), nullable=False),
    sa.Column('letter_grade', sa.String(length=4), nullable=False),
    sa.Column('grade_points', sa.Numeric(precision=3, scale=2), nullable=False),
    sa.Column('outcome', sa.String(length=10), nullable=False),
    sa.Column('division', sa.String(length=40), nullable=True),
    sa.Column('imported_by', sa.Uuid(), nullable=True),
    sa.Column('revised', sa.Integer(), server_default='0', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['enrollment_id'], ['enrollments.id'], name=op.f('fk_university_result_attempts_enrollment_id_enrollments'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['imported_by'], ['users.id'], name=op.f('fk_university_result_attempts_imported_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_university_result_attempts')),
    sa.UniqueConstraint('enrollment_id', 'attempt_no', name='uq_university_result_attempts_attempt')
    )
    op.create_index(op.f('ix_university_result_attempts_enrollment_id'), 'university_result_attempts', ['enrollment_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_university_result_attempts_enrollment_id'), table_name='university_result_attempts')
    op.drop_table('university_result_attempts')
