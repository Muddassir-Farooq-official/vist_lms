"""remove admissions: drop the admission_rounds and applicants tables

Production impact - DESTRUCTIVE, approved by the owner (admissions will be a separate portal):
- Drops the tables applicants and admission_rounds, and their enum types applicant_status and admission_round_status.
  Every admission round and applicant record on the server is permanently deleted. The deploy takes a full database
  backup (backups/pre-deploy-*.dump) just before migrating; that dump is the only copy afterwards.
- How many rows were dropped is printed in the deploy log.
- Students are NOT affected: an applicant pointed at the student it became (applicants.student_id), never the other way,
  so student records, roll numbers, logins, fees and results stay exactly as they are.
- Removes the setting row sms_on_admission and the permission admission.manage (its role links go with it).
- Downgrade recreates the two tables EMPTY (the deleted records cannot come back without restoring the backup).

Revision ID: 84f696dfc4f2
Revises: 866e27fa4edc
Create Date: 2026-11-22 18:29:11.685089

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '84f696dfc4f2'
down_revision = '866e27fa4edc'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    has = {r[0] for r in bind.execute(sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() "
                                                "AND table_name IN ('applicants', 'admission_rounds')"))}
    for t in ("applicants", "admission_rounds"):
        if t in has:
            n = bind.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar_one()
            print(f"[remove admissions] dropping {t}: {n} row(s); the pre-deploy backup keeps them")
    op.drop_index(op.f('ix_applicants_round_id'), table_name='applicants')
    op.drop_table('applicants')
    op.drop_table('admission_rounds')
    op.execute("DROP TYPE IF EXISTS applicant_status")
    op.execute("DROP TYPE IF EXISTS admission_round_status")
    op.execute("DELETE FROM policy_settings WHERE key = 'sms_on_admission'")
    op.execute("DELETE FROM permissions WHERE code = 'admission.manage'")      # role_permissions rows go with it (ON DELETE CASCADE)


def downgrade() -> None:
    # The tables come back EMPTY: the dropped records exist only in the pre-deploy backup.
    op.create_table('admission_rounds',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('term_label', sa.VARCHAR(length=50), autoincrement=False, nullable=False),
    sa.Column('program_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('seats', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('weight_matric', sa.NUMERIC(precision=5, scale=2), autoincrement=False, nullable=False),
    sa.Column('weight_inter', sa.NUMERIC(precision=5, scale=2), autoincrement=False, nullable=False),
    sa.Column('weight_test', sa.NUMERIC(precision=5, scale=2), autoincrement=False, nullable=False),
    sa.Column('min_inter_percent', sa.NUMERIC(precision=5, scale=2), server_default=sa.text("'45'::numeric"), autoincrement=False, nullable=False),
    sa.Column('closes_on', sa.DATE(), autoincrement=False, nullable=True),
    sa.Column('status', postgresql.ENUM('OPEN', 'CLOSED', name='admission_round_status'), autoincrement=False, nullable=False),
    sa.Column('merit_lists_published', sa.INTEGER(), server_default=sa.text('0'), autoincrement=False, nullable=False),
    sa.Column('created_by', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_admission_rounds_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['program_id'], ['programs.id'], name=op.f('fk_admission_rounds_program_id_programs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_admission_rounds')),
    sa.UniqueConstraint('term_label', 'program_id', name=op.f('uq_admission_rounds_term_program'), postgresql_include=[], postgresql_nulls_not_distinct=False)
    )
    op.create_table('applicants',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('round_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('application_no', sa.VARCHAR(length=30), autoincrement=False, nullable=False),
    sa.Column('first_name', sa.VARCHAR(length=100), autoincrement=False, nullable=False),
    sa.Column('last_name', sa.VARCHAR(length=100), autoincrement=False, nullable=False),
    sa.Column('cnic', sa.VARCHAR(length=15), autoincrement=False, nullable=False),
    sa.Column('contact_no', sa.VARCHAR(length=20), autoincrement=False, nullable=False),
    sa.Column('details', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=False),
    sa.Column('matric_obtained', sa.NUMERIC(precision=7, scale=2), autoincrement=False, nullable=False),
    sa.Column('matric_total', sa.NUMERIC(precision=7, scale=2), autoincrement=False, nullable=False),
    sa.Column('inter_obtained', sa.NUMERIC(precision=7, scale=2), autoincrement=False, nullable=False),
    sa.Column('inter_total', sa.NUMERIC(precision=7, scale=2), autoincrement=False, nullable=False),
    sa.Column('test_obtained', sa.NUMERIC(precision=7, scale=2), autoincrement=False, nullable=True),
    sa.Column('test_total', sa.NUMERIC(precision=7, scale=2), autoincrement=False, nullable=True),
    sa.Column('aggregate', sa.NUMERIC(precision=6, scale=3), autoincrement=False, nullable=False),
    sa.Column('status', postgresql.ENUM('SUBMITTED', 'VERIFIED', 'REJECTED', 'OFFERED', 'ADMITTED', 'EXPIRED', 'WITHDRAWN', name='applicant_status'), autoincrement=False, nullable=False),
    sa.Column('merit_list_no', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('merit_rank', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('offer_expires_on', sa.DATE(), autoincrement=False, nullable=True),
    sa.Column('remarks', sa.VARCHAR(length=300), autoincrement=False, nullable=True),
    sa.Column('student_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('created_by', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_applicants_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['round_id'], ['admission_rounds.id'], name=op.f('fk_applicants_round_id_admission_rounds'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], name=op.f('fk_applicants_student_id_students'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_applicants')),
    sa.UniqueConstraint('application_no', name=op.f('uq_applicants_application_no'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    sa.UniqueConstraint('round_id', 'cnic', name=op.f('uq_applicants_round_cnic'), postgresql_include=[], postgresql_nulls_not_distinct=False)
    )
    op.create_index(op.f('ix_applicants_round_id'), 'applicants', ['round_id'], unique=False)
