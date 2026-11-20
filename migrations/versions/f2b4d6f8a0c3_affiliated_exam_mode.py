"""Affiliated programs: who conducts the exams (the institute, or the affiliating university for some parts).

Production impact: adds to `programs` the columns exam_body (NOT NULL, default 'INSTITUTE', so every existing program
keeps working exactly as today) and university_parts (NULL); adds to `enrollment_results` the column source (NULL =
computed by the institute as today; 'UNIVERSITY' = imported from the university's result). No existing row changes.
Downgrade drops the three columns (any imported university results stay as ordinary published results).

Revision ID: f2b4d6f8a0c3
Revises: e1a3c5e7f9b2
"""
from alembic import op
import sqlalchemy as sa


revision = 'f2b4d6f8a0c3'
down_revision = 'e1a3c5e7f9b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('programs', sa.Column('exam_body', sa.String(length=12), nullable=False, server_default='INSTITUTE'))
    op.add_column('programs', sa.Column('university_parts', sa.String(length=40), nullable=True))
    op.add_column('enrollment_results', sa.Column('source', sa.String(length=12), nullable=True))


def downgrade() -> None:
    op.drop_column('enrollment_results', 'source')
    op.drop_column('programs', 'university_parts')
    op.drop_column('programs', 'exam_body')
