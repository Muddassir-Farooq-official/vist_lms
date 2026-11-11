"""Exam duty, the exam roll and unfair means cases.

Production impact: additive only, safe to run on the live database.
  - three new, empty tables: exam_duties, exam_attendance, exam_cases (and their enum types);
  - one new nullable column exam_schedules.assessment_type_id (no default, no rewrite of the table);
  - each paper that already has an invigilator gets one matching "in charge" row in exam_duties,
    so existing datesheets keep their teacher. exam_papers.invigilator_employee_id is kept as it is.
Nothing is deleted or changed in existing rows. Fast on any realistic data size (a few hundred papers).
Downgrade drops the three new tables, their enum types and the new column (duty, roll and case
records made after the upgrade would be lost; the invigilator column is untouched).

Revision ID: c0e2a4b6d8f0
Revises: b9d1f3a5c7e9
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c0e2a4b6d8f0'
down_revision = 'b9d1f3a5c7e9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('exam_attendance',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('paper_id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.Enum('PRESENT', 'ABSENT', 'LATE', name='exam_attendance_status'), nullable=False),
    sa.Column('marked_by', sa.Uuid(), nullable=True),
    sa.Column('marked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['marked_by'], ['users.id'], name=op.f('fk_exam_attendance_marked_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['paper_id'], ['exam_papers.id'], name=op.f('fk_exam_attendance_paper_id_exam_papers'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_exam_attendance_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_exam_attendance')),
    sa.UniqueConstraint('paper_id', 'student_id', name='uq_exam_attendance_paper_student')
    )
    op.create_index(op.f('ix_exam_attendance_paper_id'), 'exam_attendance', ['paper_id'], unique=False)
    op.create_index(op.f('ix_exam_attendance_student_id'), 'exam_attendance', ['student_id'], unique=False)
    op.create_table('exam_cases',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('paper_id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('note', sa.String(length=1000), nullable=False),
    sa.Column('reported_by', sa.Uuid(), nullable=True),
    sa.Column('reported_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('status', sa.Enum('OPEN', 'DECIDED', name='exam_case_status'), nullable=False),
    sa.Column('decision', sa.String(length=1000), nullable=True),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name=op.f('fk_exam_cases_decided_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['paper_id'], ['exam_papers.id'], name=op.f('fk_exam_cases_paper_id_exam_papers'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reported_by'], ['users.id'], name=op.f('fk_exam_cases_reported_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_exam_cases_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_exam_cases'))
    )
    op.create_index(op.f('ix_exam_cases_paper_id'), 'exam_cases', ['paper_id'], unique=False)
    op.create_index(op.f('ix_exam_cases_student_id'), 'exam_cases', ['student_id'], unique=False)
    op.create_table('exam_duties',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('paper_id', sa.Uuid(), nullable=False),
    sa.Column('employee_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.Enum('IN_CHARGE', 'ASSISTANT', name='exam_duty_role'), nullable=False),
    sa.Column('assigned_by', sa.Uuid(), nullable=True),
    sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['assigned_by'], ['users.id'], name=op.f('fk_exam_duties_assigned_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['employee_id'], ['employees.id'], name=op.f('fk_exam_duties_employee_id_employees'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['paper_id'], ['exam_papers.id'], name=op.f('fk_exam_duties_paper_id_exam_papers'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_exam_duties')),
    sa.UniqueConstraint('paper_id', 'employee_id', name='uq_exam_duties_paper_employee')
    )
    op.create_index('ix_exam_duties_employee_id', 'exam_duties', ['employee_id'], unique=False)
    op.create_index(op.f('ix_exam_duties_paper_id'), 'exam_duties', ['paper_id'], unique=False)
    op.add_column('exam_schedules', sa.Column('assessment_type_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f('fk_exam_schedules_assessment_type_id_assessment_types'), 'exam_schedules', 'assessment_types', ['assessment_type_id'], ['id'], ondelete='SET NULL')
    # the invigilator already chosen for a paper becomes its teacher in charge
    op.execute("""
        INSERT INTO exam_duties (id, paper_id, employee_id, role, assigned_at)
        SELECT gen_random_uuid(), p.id, p.invigilator_employee_id, 'IN_CHARGE', now()
        FROM exam_papers p
        WHERE p.invigilator_employee_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM exam_duties d WHERE d.paper_id = p.id AND d.employee_id = p.invigilator_employee_id)
    """)


def downgrade() -> None:
    op.drop_constraint(op.f('fk_exam_schedules_assessment_type_id_assessment_types'), 'exam_schedules', type_='foreignkey')
    op.drop_column('exam_schedules', 'assessment_type_id')
    op.drop_index(op.f('ix_exam_duties_paper_id'), table_name='exam_duties')
    op.drop_index('ix_exam_duties_employee_id', table_name='exam_duties')
    op.drop_table('exam_duties')
    op.drop_index(op.f('ix_exam_cases_student_id'), table_name='exam_cases')
    op.drop_index(op.f('ix_exam_cases_paper_id'), table_name='exam_cases')
    op.drop_table('exam_cases')
    op.drop_index(op.f('ix_exam_attendance_student_id'), table_name='exam_attendance')
    op.drop_index(op.f('ix_exam_attendance_paper_id'), table_name='exam_attendance')
    op.drop_table('exam_attendance')
    for name in ("exam_duty_role", "exam_case_status", "exam_attendance_status"):
        op.execute(f"DROP TYPE IF EXISTS {name}")
