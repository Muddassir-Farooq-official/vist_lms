"""fix timezone-naive datetime columns on import_jobs and student_status_history

Revision ID: 3c0a16bd1234
Revises: 221008dd59b1
Create Date: 2026-08-19 05:49:03.620441

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '3c0a16bd1234'
down_revision = '221008dd59b1'
branch_labels = None
depends_on = None

# NOTE: autogenerate also reported several false-positive diffs (dropping
# the trigram/functional indexes and CNIC check constraints from the prior
# hand-written migration) because those objects were created via raw SQL
# and aren't reflected in the ORM metadata Alembic compares against. Those
# lines were removed from this revision — the only real drift was the three
# timezone-naive datetime columns below (Section 11: UTC timestamps
# everywhere).


def upgrade() -> None:
    op.alter_column('import_jobs', 'started_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=True)
    op.alter_column('import_jobs', 'finished_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=True)
    op.alter_column('student_status_history', 'changed_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False)


def downgrade() -> None:
    op.alter_column('student_status_history', 'changed_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False)
    op.alter_column('import_jobs', 'finished_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=True)
    op.alter_column('import_jobs', 'started_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=True)
