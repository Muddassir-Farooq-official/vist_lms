"""graduation lists, degree and transcript tracking, alumni details, directory and surveys

Production impact: creates six new, empty tables (graduation_lists, graduation_entries, document_issues,
alumni_profiles, alumni_surveys, alumni_survey_responses). No existing table or row is changed. Nobody is
graduated by deploying: graduation happens only when an administrator confirms a list. The new "Alumni (graduate)"
role and three permissions come from the permission seed script the deploy already runs.
Downgrade drops only these tables (and any lists, tracking and alumni details entered since).

Revision ID: 866e27fa4edc
Revises: 3df8fd9e828e
Create Date: 2026-11-22 14:54:51.687055

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '866e27fa4edc'
down_revision = '3df8fd9e828e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('alumni_surveys',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('intro', sa.Text(), nullable=True),
    sa.Column('questions', sa.JSON(), nullable=False),
    sa.Column('is_open', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_alumni_surveys_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alumni_surveys'))
    )
    op.create_table('graduation_lists',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('program_id', sa.Uuid(), nullable=False),
    sa.Column('graduation_date', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('confirmed_by', sa.Uuid(), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['confirmed_by'], ['users.id'], name=op.f('fk_graduation_lists_confirmed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_graduation_lists_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['program_id'], ['programs.id'], name=op.f('fk_graduation_lists_program_id_programs'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_graduation_lists'))
    )
    op.create_table('alumni_profiles',
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('employer', sa.String(length=150), nullable=True),
    sa.Column('job_title', sa.String(length=120), nullable=True),
    sa.Column('city', sa.String(length=80), nullable=True),
    sa.Column('country', sa.String(length=80), nullable=True),
    sa.Column('further_study', sa.String(length=200), nullable=True),
    sa.Column('email', sa.String(length=150), nullable=True),
    sa.Column('phone', sa.String(length=30), nullable=True),
    sa.Column('linkedin', sa.String(length=200), nullable=True),
    sa.Column('in_directory', sa.Boolean(), nullable=False),
    sa.Column('share_contact', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_alumni_profiles_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('student_id', name=op.f('pk_alumni_profiles'))
    )
    op.create_table('alumni_survey_responses',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('survey_id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('answers', sa.JSON(), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_alumni_survey_responses_student_id_students'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['survey_id'], ['alumni_surveys.id'], name=op.f('fk_alumni_survey_responses_survey_id_alumni_surveys'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alumni_survey_responses')),
    sa.UniqueConstraint('survey_id', 'student_id', name='uq_alumni_survey_responses_once')
    )
    op.create_index(op.f('ix_alumni_survey_responses_survey_id'), 'alumni_survey_responses', ['survey_id'], unique=False)
    op.create_table('document_issues',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('serial_no', sa.String(length=40), nullable=True),
    sa.Column('copies', sa.Integer(), nullable=False),
    sa.Column('requested_by_student', sa.Boolean(), nullable=False),
    sa.Column('handed_to', sa.String(length=120), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('history', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_document_issues_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_document_issues'))
    )
    op.create_index('ix_document_issues_status', 'document_issues', ['status'], unique=False)
    op.create_index('ix_document_issues_student', 'document_issues', ['student_id'], unique=False)
    op.create_table('graduation_entries',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('list_id', sa.Uuid(), nullable=False),
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('cgpa', sa.Numeric(precision=4, scale=2), nullable=True),
    sa.Column('credits', sa.Integer(), nullable=True),
    sa.Column('problems', sa.JSON(), nullable=True),
    sa.Column('included', sa.Boolean(), nullable=False),
    sa.Column('result', sa.String(length=10), nullable=True),
    sa.Column('result_note', sa.String(length=300), nullable=True),
    sa.ForeignKeyConstraint(['list_id'], ['graduation_lists.id'], name=op.f('fk_graduation_entries_list_id_graduation_lists'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_graduation_entries_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_graduation_entries')),
    sa.UniqueConstraint('list_id', 'student_id', name='uq_graduation_entries_once')
    )
    op.create_index(op.f('ix_graduation_entries_list_id'), 'graduation_entries', ['list_id'], unique=False)
    op.create_index(op.f('ix_graduation_entries_student_id'), 'graduation_entries', ['student_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_graduation_entries_student_id'), table_name='graduation_entries')
    op.drop_index(op.f('ix_graduation_entries_list_id'), table_name='graduation_entries')
    op.drop_table('graduation_entries')
    op.drop_index('ix_document_issues_student', table_name='document_issues')
    op.drop_index('ix_document_issues_status', table_name='document_issues')
    op.drop_table('document_issues')
    op.drop_index(op.f('ix_alumni_survey_responses_survey_id'), table_name='alumni_survey_responses')
    op.drop_table('alumni_survey_responses')
    op.drop_table('alumni_profiles')
    op.drop_table('graduation_lists')
    op.drop_table('alumni_surveys')
