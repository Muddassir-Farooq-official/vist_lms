"""admissions: admission rounds, applicants, and the admission.manage permission (ADMIN)

Revision ID: fd3b4c5d6e7f
Revises: fc2a3b4c5d6e
"""
import sqlalchemy as sa
from alembic import op

revision = "fd3b4c5d6e7f"
down_revision = "fc2a3b4c5d6e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    rstatus = sa.Enum("OPEN", "CLOSED", name="admission_round_status")
    astatus = sa.Enum("SUBMITTED", "VERIFIED", "REJECTED", "OFFERED", "ADMITTED", "EXPIRED", "WITHDRAWN", name="applicant_status")
    op.create_table(
        "admission_rounds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("term_label", sa.String(50), nullable=False),
        sa.Column("program_id", sa.Uuid(), sa.ForeignKey("programs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("weight_matric", sa.Numeric(5, 2), nullable=False),
        sa.Column("weight_inter", sa.Numeric(5, 2), nullable=False),
        sa.Column("weight_test", sa.Numeric(5, 2), nullable=False),
        sa.Column("min_inter_percent", sa.Numeric(5, 2), nullable=False, server_default="45"),
        sa.Column("closes_on", sa.Date(), nullable=True),
        sa.Column("status", rstatus, nullable=False),
        sa.Column("merit_lists_published", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("term_label", "program_id", name="uq_admission_rounds_term_program"),
    )
    op.create_table(
        "applicants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("round_id", sa.Uuid(), sa.ForeignKey("admission_rounds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_no", sa.String(30), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("cnic", sa.String(15), nullable=False),
        sa.Column("contact_no", sa.String(20), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("matric_obtained", sa.Numeric(7, 2), nullable=False),
        sa.Column("matric_total", sa.Numeric(7, 2), nullable=False),
        sa.Column("inter_obtained", sa.Numeric(7, 2), nullable=False),
        sa.Column("inter_total", sa.Numeric(7, 2), nullable=False),
        sa.Column("test_obtained", sa.Numeric(7, 2), nullable=True),
        sa.Column("test_total", sa.Numeric(7, 2), nullable=True),
        sa.Column("aggregate", sa.Numeric(6, 3), nullable=False),
        sa.Column("status", astatus, nullable=False),
        sa.Column("merit_list_no", sa.Integer(), nullable=True),
        sa.Column("merit_rank", sa.Integer(), nullable=True),
        sa.Column("offer_expires_on", sa.Date(), nullable=True),
        sa.Column("remarks", sa.String(300), nullable=True),
        sa.Column("student_id", sa.Uuid(), sa.ForeignKey("students.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("round_id", "cnic", name="uq_applicants_round_cnic"),
        sa.UniqueConstraint("application_no", name="uq_applicants_application_no"),
    )
    op.create_index("ix_applicants_round_id", "applicants", ["round_id"])
    op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), 'admission.manage', "
                       "'Run admissions', now(), now() WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'admission.manage')"))
    op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
                       "WHERE r.code = 'ADMIN' AND p.code = 'admission.manage' AND NOT EXISTS "
                       "(SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)"))


def downgrade() -> None:
    op.drop_table("applicants")
    op.drop_table("admission_rounds")
    sa.Enum(name="applicant_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="admission_round_status").drop(op.get_bind(), checkfirst=True)
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'admission.manage')")
    op.execute("DELETE FROM permissions WHERE code = 'admission.manage'")