"""exam paper files: teachers send the question paper (PDF or Word), the exam office prints it

Production impact: creates one new, empty table (exam_paper_files). No existing table or row is changed. The files
themselves are stored under the existing storage folder (exam-papers/). Downgrade drops only this table; files already
on disk are left in place.

Revision ID: e2a74a054eb7
Revises: f0cd9cb45e8a
Create Date: 2026-11-22 18:22:29.459609

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2a74a054eb7'
down_revision = 'f0cd9cb45e8a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('exam_paper_files',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('exam_paper_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('storage_key', sa.String(length=300), nullable=False),
    sa.Column('file_name', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=120), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('uploaded_by', sa.Uuid(), nullable=True),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('return_note', sa.String(length=500), nullable=True),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_exam_paper_files_decided_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['exam_paper_id'], ['exam_papers.id'], name=op.f('fk_exam_paper_files_exam_paper_id_exam_papers'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], name=op.f('fk_exam_paper_files_uploaded_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_exam_paper_files'))
    )
    op.create_index('ix_exam_paper_files_paper', 'exam_paper_files', ['exam_paper_id', 'version'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_exam_paper_files_paper', table_name='exam_paper_files')
    op.drop_table('exam_paper_files')
