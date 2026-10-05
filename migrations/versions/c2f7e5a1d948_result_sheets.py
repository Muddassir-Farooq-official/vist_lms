"""results: marks are submitted, approved and published before students see them

Revision ID: c2f7e5a1d948
Revises: b1e6d2a9c473
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c2f7e5a1d948"
down_revision = "b1e6d2a9c473"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE notification_category ADD VALUE IF NOT EXISTS 'RESULT'")
    op.create_table(
        "result_sheets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("course_offering_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", postgresql.ENUM("DRAFT", "SUBMITTED", "RETURNED", "APPROVED", "PUBLISHED", name="result_sheet_status"), nullable=False),
        sa.Column("submitted_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("returned_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("returned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("return_reason", sa.Text(), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correction_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resubmission_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("course_offering_id", name="uq_result_sheets_offering"),
    )
    # Results that already exist stay visible: every course that already has marks is treated as already published.
    op.execute("""
        INSERT INTO result_sheets (id, course_offering_id, status, published_at, correction_count, resubmission_count, created_at, updated_at)
        SELECT gen_random_uuid(), x.course_offering_id, 'PUBLISHED', now(), 0, 0, now(), now()
        FROM (SELECT DISTINCT oa.course_offering_id FROM offering_assessments oa JOIN student_marks sm ON sm.offering_assessment_id = oa.id) x
    """)
    op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), 'result.approve', "
                       "'Approve, return and publish a course''s marks', now(), now() WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'result.approve')"))
    op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = 'ADMIN' AND p.code = 'result.approve' "
                       "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)"))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'result.approve')")
    op.execute("DELETE FROM permissions WHERE code = 'result.approve'")
    op.drop_table("result_sheets")
    op.execute("DROP TYPE IF EXISTS result_sheet_status")
    # (PostgreSQL cannot remove a value from the notification_category enum; RESULT is left in place, unused.)