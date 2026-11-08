"""Course & teacher feedback from students.

Revision ID: b3d5f7a9c1e2
Revises: a1c3e5f7b9d2
Create Date: 2026-11-08 16:01:43.194165

ADDITIVE and safe to run while the old version of the app is still serving: four new, empty tables. No existing table
or row is touched. The default questions are added by the app the first time the survey is opened. Downgrade drops the
four tables (and any feedback given).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3d5f7a9c1e2'
down_revision = 'a1c3e5f7b9d2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('feedback_questions',
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('text', sa.String(length=300), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_feedback_questions'))
    )
    op.create_table('feedback_answers',
    sa.Column('course_offering_id', sa.Uuid(), nullable=False),
    sa.Column('question_id', sa.Uuid(), nullable=False),
    sa.Column('score', sa.SmallInteger(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('score BETWEEN 1 AND 5', name=op.f('ck_feedback_answers_score_1_to_5')),
    sa.ForeignKeyConstraint(['course_offering_id'], ['course_offerings.id'], name=op.f('fk_feedback_answers_course_offering_id_course_offerings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['question_id'], ['feedback_questions.id'], name=op.f('fk_feedback_answers_question_id_feedback_questions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_feedback_answers'))
    )
    op.create_index('ix_feedback_answers_offering', 'feedback_answers', ['course_offering_id'], unique=False)
    op.create_table('feedback_comments',
    sa.Column('course_offering_id', sa.Uuid(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['course_offering_id'], ['course_offerings.id'], name=op.f('fk_feedback_comments_course_offering_id_course_offerings'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_feedback_comments'))
    )
    op.create_index('ix_feedback_comments_offering', 'feedback_comments', ['course_offering_id'], unique=False)
    op.create_table('feedback_submissions',
    sa.Column('student_id', sa.Uuid(), nullable=False),
    sa.Column('course_offering_id', sa.Uuid(), nullable=False),
    sa.Column('submitted_on', sa.Date(), nullable=False),
    sa.ForeignKeyConstraint(['course_offering_id'], ['course_offerings.id'], name=op.f('fk_feedback_submissions_course_offering_id_course_offerings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_feedback_submissions_student_id_students'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('student_id', 'course_offering_id', name=op.f('pk_feedback_submissions'))
    )


def downgrade() -> None:
    op.drop_table('feedback_submissions')
    op.drop_index('ix_feedback_comments_offering', table_name='feedback_comments')
    op.drop_table('feedback_comments')
    op.drop_index('ix_feedback_answers_offering', table_name='feedback_answers')
    op.drop_table('feedback_answers')
    op.drop_table('feedback_questions')
