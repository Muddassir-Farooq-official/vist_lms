"""WhatsApp: its settings, and which channel carried each message.

Revision ID: f7b9d1e3a5c7
Revises: e6a8c0d2f4b6
Create Date: 2026-11-09 13:19:23.172193

ADDITIVE and safe to run while the old version of the app is still serving:
* whatsapp_settings: a new, empty one-row table (WhatsApp stays OFF until an administrator sets it up; tokens are sealed).
* sms_messages: three new nullable columns (channel, attachment, whatsapp_error). Adding nullable columns without a default
  does not rewrite the table. Existing messages keep NULL, which the screens show as SMS.
Downgrade drops the table and the three columns (the record of which messages went by WhatsApp is lost; the messages stay).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f7b9d1e3a5c7'
down_revision = 'e6a8c0d2f4b6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('whatsapp_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('mode', sa.String(length=10), server_default='OFF', nullable=False),
    sa.Column('send_documents', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('meta_phone_number_id', sa.String(length=40), nullable=True),
    sa.Column('meta_token_enc', sa.Text(), nullable=True),
    sa.Column('meta_template_text', sa.String(length=100), nullable=True),
    sa.Column('meta_template_document', sa.String(length=100), nullable=True),
    sa.Column('meta_template_language', sa.String(length=10), server_default='en', nullable=False),
    sa.Column('link_url', sa.String(length=1000), nullable=True),
    sa.Column('link_method', sa.String(length=6), server_default='GET', nullable=False),
    sa.Column('link_body', sa.String(length=2000), nullable=True),
    sa.Column('link_api_key_enc', sa.Text(), nullable=True),
    sa.Column('link_sender', sa.String(length=60), nullable=True),
    sa.Column('link_success_marker', sa.String(length=100), nullable=True),
    sa.Column('public_base', sa.String(length=200), nullable=True),
    sa.Column('last_test_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_test_ok', sa.Boolean(), nullable=True),
    sa.Column('last_test_error', sa.String(length=500), nullable=True),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('id = 1', name=op.f('ck_whatsapp_settings_single_row')),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_whatsapp_settings_updated_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_whatsapp_settings'))
    )
    op.add_column('sms_messages', sa.Column('channel', sa.String(length=10), nullable=True))
    op.add_column('sms_messages', sa.Column('attachment', sa.String(length=80), nullable=True))
    op.add_column('sms_messages', sa.Column('whatsapp_error', sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column('sms_messages', 'whatsapp_error')
    op.drop_column('sms_messages', 'attachment')
    op.drop_column('sms_messages', 'channel')
    op.drop_table('whatsapp_settings')
