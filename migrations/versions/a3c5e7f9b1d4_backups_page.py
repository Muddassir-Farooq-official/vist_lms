"""Backups page in the admin panel: what the server's backups did, and admins' requests the server carries out.

Production impact: two NEW tables (backup_runs, server_jobs), empty at first. Nothing existing is changed. The server
fills backup_runs from the next backup on (scripts/server/backup.sh) and handles server_jobs every minute
(scripts/server/sites.sh jobs). Downgrade drops the two tables (only this page's history is lost; the backups
themselves are on the backup account and the server, untouched).

Revision ID: a3c5e7f9b1d4
Revises: f2b4d6f8a0c3
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'a3c5e7f9b1d4'
down_revision = 'f2b4d6f8a0c3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'backup_runs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('kind', sa.String(length=12), nullable=False),
        sa.Column('ok', sa.Boolean(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('detail', sa.String(length=500), nullable=True),
        sa.Column('db_bytes', sa.BigInteger(), nullable=True),
        sa.Column('files_bytes', sa.BigInteger(), nullable=True),
        sa.Column('remote_bytes', sa.BigInteger(), nullable=True),
        sa.Column('remote_objects', sa.Integer(), nullable=True),
        sa.Column('copies', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_backup_runs')),
    )
    op.create_index('ix_backup_runs_finished_at', 'backup_runs', ['finished_at'])
    op.create_table(
        'server_jobs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=10), server_default='PENDING', nullable=False),
        sa.Column('requested_by', sa.Uuid(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('file_name', sa.String(length=120), nullable=True),
        sa.Column('file_bytes', sa.BigInteger(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('downloaded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('detail', sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(['requested_by'], ['users.id'], name=op.f('fk_server_jobs_requested_by_users'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_server_jobs')),
    )
    op.create_index('ix_server_jobs_status', 'server_jobs', ['status'])


def downgrade() -> None:
    op.drop_index('ix_server_jobs_status', table_name='server_jobs')
    op.drop_table('server_jobs')
    op.drop_index('ix_backup_runs_finished_at', table_name='backup_runs')
    op.drop_table('backup_runs')
