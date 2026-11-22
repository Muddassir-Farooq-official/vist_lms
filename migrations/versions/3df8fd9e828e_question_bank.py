"""question bank: teachers' questions and the exam office's papers

Production impact: creates three new, empty tables (qbank_questions, qbank_papers, qbank_paper_items). No existing
table or row is changed. Downgrade drops only these tables (and any questions and papers entered since).

Revision ID: 3df8fd9e828e
Revises: f0cd9cb45e8a
Create Date: 2026-11-22 12:29:42.167639

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3df8fd9e828e'
down_revision = 'f0cd9cb45e8a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('qbank_questions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('course_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=6), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('options', sa.JSON(), nullable=True),
    sa.Column('answer', sa.Text(), nullable=True),
    sa.Column('clo', sa.String(length=12), nullable=True),
    sa.Column('bloom', sa.String(length=4), nullable=True),
    sa.Column('difficulty', sa.String(length=6), nullable=False),
    sa.Column('marks', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('times_used', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['course_id'], ['courses.id'], name=op.f('fk_qbank_questions_course_id_courses'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_qbank_questions_created_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_qbank_questions'))
    )
    op.create_index('ix_qbank_questions_course_kind', 'qbank_questions', ['course_id', 'kind'], unique=False)
    op.create_table('qbank_papers',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('course_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('exam_paper_id', sa.Uuid(), nullable=True),
    sa.Column('duration_minutes', sa.Integer(), nullable=True),
    sa.Column('instructions', sa.String(length=1000), nullable=True),
    sa.Column('status', sa.String(length=6), nullable=False),
    sa.Column('blueprint', sa.JSON(), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finalised_by', sa.Uuid(), nullable=True),
    sa.Column('finalised_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['course_id'], ['courses.id'], name=op.f('fk_qbank_papers_course_id_courses'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_qbank_papers_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['exam_paper_id'], ['exam_papers.id'], name=op.f('fk_qbank_papers_exam_paper_id_exam_papers'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['finalised_by'], ['users.id'], name=op.f('fk_qbank_papers_finalised_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_qbank_papers'))
    )
    op.create_index(op.f('ix_qbank_papers_course_id'), 'qbank_papers', ['course_id'], unique=False)
    op.create_table('qbank_paper_items',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('paper_id', sa.Uuid(), nullable=False),
    sa.Column('question_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('marks', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.ForeignKeyConstraint(['paper_id'], ['qbank_papers.id'], name=op.f('fk_qbank_paper_items_paper_id_qbank_papers'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['question_id'], ['qbank_questions.id'], name=op.f('fk_qbank_paper_items_question_id_qbank_questions'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_qbank_paper_items')),
    sa.UniqueConstraint('paper_id', 'question_id', name='uq_qbank_paper_items_once')
    )
    op.create_index(op.f('ix_qbank_paper_items_paper_id'), 'qbank_paper_items', ['paper_id'], unique=False)
    op.create_index(op.f('ix_qbank_paper_items_question_id'), 'qbank_paper_items', ['question_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_qbank_paper_items_question_id'), table_name='qbank_paper_items')
    op.drop_index(op.f('ix_qbank_paper_items_paper_id'), table_name='qbank_paper_items')
    op.drop_table('qbank_paper_items')
    op.drop_index(op.f('ix_qbank_papers_course_id'), table_name='qbank_papers')
    op.drop_table('qbank_papers')
    op.drop_index('ix_qbank_questions_course_kind', table_name='qbank_questions')
    op.drop_table('qbank_questions')
