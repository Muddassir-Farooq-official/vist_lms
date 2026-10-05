"""applications: students and employees apply to the administration (leave, certificates, concessions...) and follow them to a decision

Revision ID: e7b3a9d2f416
Revises: d6a2f8c1e305
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e7b3a9d2f416"
down_revision = "d6a2f8c1e305"
branch_labels = None
depends_on = None

# name, description, students, employees, dates, attachment, sort
DEFAULT_TYPES = [
    ("Leave application", "Say the dates you will be away and why. The administration approves or declines it.", True, True, True, False, 10),
    ("Sick leave", "Give the dates. Attach a medical certificate if you have one.", True, True, True, False, 20),
    ("Short leave (a few hours)", "Choose the day and say the hours in the details.", False, True, True, False, 30),
    ("Fee concession or installment request", "Explain your situation and what you are asking for (a concession, or paying in installments).", True, False, False, False, 40),
    ("Certificate or transcript request", "Say which document you need, and what it is for.", True, False, False, False, 50),
    ("Course or section change request", "Say which course or section, and the reason for the change.", True, False, False, False, 60),
    ("Salary certificate or experience letter", "Say which letter you need and who it is addressed to.", False, True, False, False, 70),
    ("Advance salary request", "Say the amount and when you will be able to repay it.", False, True, False, False, 80),
    ("Other request", "Anything else you need to ask the administration.", True, True, False, False, 999),
]


def upgrade() -> None:
    op.execute("ALTER TYPE notification_category ADD VALUE IF NOT EXISTS 'APPLICATION'")
    op.create_table(
        "application_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("for_students", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("for_employees", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("requires_dates", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requires_attachment", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("yearly_limit_days", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_application_types_name"),
    )
    op.create_table(
        "applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("serial_no", sa.Integer(), sa.Identity(), nullable=False, unique=True),
        sa.Column("type_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("application_types.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("applicant_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("applicant_kind", postgresql.ENUM("STUDENT", "EMPLOYEE", name="applicant_kind"), nullable=False),
        sa.Column("applicant_ref_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("from_date", sa.Date(), nullable=True),
        sa.Column("to_date", sa.Date(), nullable=True),
        sa.Column("status", postgresql.ENUM("SUBMITTED", "UNDER_REVIEW", "MORE_INFO_NEEDED", "APPROVED", "REJECTED", "WITHDRAWN", name="application_status"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_applications_applicant", "applications", ["applicant_user_id"])
    op.create_index("ix_applications_status", "applications", ["status"])
    op.create_table(
        "application_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_application_attachments_application", "application_attachments", ["application_id"])
    op.create_table(
        "application_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_application_comments_application", "application_comments", ["application_id"])

    # Starting list of things people can apply for (the administrator edits it afterwards).
    for name, description, students, employees, dates, attachment, order in DEFAULT_TYPES:
        op.execute(sa.text(
            "INSERT INTO application_types (id, name, description, for_students, for_employees, requires_dates, requires_attachment, is_active, sort_order, created_at, updated_at) "
            "SELECT gen_random_uuid(), :n, :d, :s, :e, :dt, :a, true, :o, now(), now() WHERE NOT EXISTS (SELECT 1 FROM application_types WHERE name = :n)"
        ).bindparams(n=name, d=description, s=students, e=employees, dt=dates, a=attachment, o=order))

    # Permissions for existing installs (the seed script does the same for fresh ones).
    grants = [("application.submit", "Submit applications (leave, certificates...) to the administration",
               ["STUDENT", "TEACHER", "LIBRARIAN", "ACCOUNTS_OFFICER", "HOSTEL_WARDEN"]),
              ("application.manage", "Review, approve and reject applications", ["ADMIN"])]
    for code, description, roles in grants:
        op.execute(sa.text(
            "INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), :c, :d, now(), now() "
            "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :c)").bindparams(c=code, d=description))
        for role in roles:
            op.execute(sa.text(
                "INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p WHERE r.code = :r AND p.code = :c "
                "AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)").bindparams(r=role, c=code))


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code IN ('application.submit', 'application.manage'))")
    op.execute("DELETE FROM permissions WHERE code IN ('application.submit', 'application.manage')")
    op.drop_index("ix_application_comments_application", table_name="application_comments")
    op.drop_table("application_comments")
    op.drop_index("ix_application_attachments_application", table_name="application_attachments")
    op.drop_table("application_attachments")
    op.drop_index("ix_applications_status", table_name="applications")
    op.drop_index("ix_applications_applicant", table_name="applications")
    op.drop_table("applications")
    op.drop_table("application_types")
    for name in ("application_status", "applicant_kind"):
        op.execute(f"DROP TYPE IF EXISTS {name}")
    # (PostgreSQL cannot remove a value from the notification_category enum; it is left in place, unused.)