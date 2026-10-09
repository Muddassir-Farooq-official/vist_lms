"""exams: datesheets (exam_schedules), their papers (exam_papers), and the exam.schedule.manage permission
(ADMIN + EXAM_CONTROLLER; the exam controller also gets course.view to pick the courses)

Revision ID: f9d0e1f2a3b4
Revises: f8c9d0e1f2a3
"""
import sqlalchemy as sa
from alembic import op

revision = "f9d0e1f2a3b4"
down_revision = "f8c9d0e1f2a3"
branch_labels = None
depends_on = None


def _grant(role: str, code: str) -> None:
    op.execute(sa.text("INSERT INTO role_permissions (role_id, permission_id) SELECT r.id, p.id FROM roles r, permissions p "
                       "WHERE r.code = :r AND p.code = :c AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)")
               .bindparams(r=role, c=code))


def upgrade() -> None:
    status = sa.Enum("DRAFT", "PUBLISHED", name="exam_schedule_status")
    op.create_table(
        "exam_schedules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("term_label", sa.String(50), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("instructions", sa.String(1000), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("term_label", "name", name="uq_exam_schedules_term_name"),
    )
    op.create_table(
        "exam_papers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("schedule_id", sa.Uuid(), sa.ForeignKey("exam_schedules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_offering_id", sa.Uuid(), sa.ForeignKey("course_offerings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("exam_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("room", sa.String(50), nullable=False),
        sa.Column("invigilator_employee_id", sa.Uuid(), sa.ForeignKey("employees.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("schedule_id", "course_offering_id", name="uq_exam_papers_schedule_offering"),
    )
    op.create_index("ix_exam_papers_schedule_id", "exam_papers", ["schedule_id"])
    op.create_index("ix_exam_papers_exam_date", "exam_papers", ["exam_date"])
    op.execute(sa.text("INSERT INTO permissions (id, code, description, created_at, updated_at) SELECT gen_random_uuid(), 'exam.schedule.manage', "
                       "'Build and publish exam datesheets; print admit cards', now(), now() WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'exam.schedule.manage')"))
    for role in ("ADMIN", "EXAM_CONTROLLER"):
        _grant(role, "exam.schedule.manage")
    _grant("EXAM_CONTROLLER", "course.view")


def downgrade() -> None:
    op.drop_table("exam_papers")
    op.drop_table("exam_schedules")
    sa.Enum(name="exam_schedule_status").drop(op.get_bind(), checkfirst=True)
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'exam.schedule.manage')")
    op.execute("DELETE FROM permissions WHERE code = 'exam.schedule.manage'")
    