"""employee email, blood group, marital status, employment type, emergency contact

Revision ID: a4e7c2b95d18
Revises: f9d2a6c41e7b
"""
from alembic import op
import sqlalchemy as sa

revision = "a4e7c2b95d18"
down_revision = "f9d2a6c41e7b"
branch_labels = None
depends_on = None

_COLS = [
    ("email", 255), ("blood_group", 5), ("marital_status", 20), ("employment_type", 20),
    ("emergency_contact_name", 150), ("emergency_contact_no", 20),
]


def upgrade() -> None:
    for name, size in _COLS:
        op.add_column("employees", sa.Column(name, sa.String(size), nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLS):
        op.drop_column("employees", name)