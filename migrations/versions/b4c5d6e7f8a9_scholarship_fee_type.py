"""fees: a scholarship / discount can be limited to one fee TYPE (e.g. Tuition), for every semester

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8

Production impact: adds one empty, optional column scholarships.fee_type_id (a link to fee_types) and its index.
No existing row changes: every existing scholarship keeps fee_type_id empty, so it applies exactly as before.
Adding a nullable column with no default is instant in PostgreSQL, even on a large table; safe to run while the site is up.
Downgrade drops the column (any "fee type only" choice made after the upgrade is lost; discounts already given stay in the ledger).
"""
import sqlalchemy as sa
from alembic import op

revision = "b4c5d6e7f8a9"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scholarships", sa.Column("fee_type_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_scholarships_fee_type_id_fee_types", "scholarships", "fee_types", ["fee_type_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_scholarships_fee_type", "scholarships", ["fee_type_id"])


def downgrade() -> None:
    op.drop_index("ix_scholarships_fee_type", table_name="scholarships")
    op.drop_constraint("fk_scholarships_fee_type_id_fee_types", "scholarships", type_="foreignkey")
    op.drop_column("scholarships", "fee_type_id")