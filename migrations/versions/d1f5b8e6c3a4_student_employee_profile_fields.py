"""student/employee profile fields (photo, certificate, qualifications, address) + roll number format change

Revision ID: d1f5b8e6c3a4
Revises: c9e1a5d47f2b
Create Date: 2026-08-29 00:00:00.000000

Several independent additions, bundled into one migration since they're
all part of the same approved profile-page feature:

1. Roll number format changed from '<PROGRAM_CODE>-<YEAR>-<SEQ>' (e.g.
   'BSN-2026-0001') to '<2-digit year><seq>' (e.g. '26001') — a single
   institution-wide admission sequence per year, not per-program.
   Existing students' roll numbers/usernames are NOT renamed — same
   policy as every prior identifier-scheme change in this project (see
   c7f3a1d95e6b) — only new admissions from this point on use the new
   format. Nothing to migrate structurally here; the column is already
   a plain string, this is a code-only change (app/core/sequences.py).

2. students / employees gain: photo_storage_key, photo_original_name,
   certificate_storage_key, certificate_original_name (uploaded-file
   pointers — see app/core/storage.py).

3. employees gains religion, province, district, city, address_line —
   did not exist on Employee at all before (only Student had these).

4. New `qualifications` table — one row per academic qualification
   (degree/institute/year/marks), shared between STUDENT and EMPLOYEE
   via the same polymorphic party_type/party_id shape used by Fines and
   Complaints.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd1f5b8e6c3a4'
down_revision = 'c9e1a5d47f2b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- students: uploaded-file fields ---
    op.add_column('students', sa.Column('photo_storage_key', sa.String(length=255), nullable=True))
    op.add_column('students', sa.Column('photo_original_name', sa.String(length=255), nullable=True))
    op.add_column('students', sa.Column('certificate_storage_key', sa.String(length=255), nullable=True))
    op.add_column('students', sa.Column('certificate_original_name', sa.String(length=255), nullable=True))

    # --- employees: address/religion (new) + uploaded-file fields ---
    op.add_column('employees', sa.Column('religion', sa.String(length=50), nullable=True))
    op.add_column('employees', sa.Column('province', sa.String(length=100), nullable=True))
    op.add_column('employees', sa.Column('district', sa.String(length=100), nullable=True))
    op.add_column('employees', sa.Column('city', sa.String(length=100), nullable=True))
    op.add_column('employees', sa.Column('address_line', sa.String(length=255), nullable=True))
    op.add_column('employees', sa.Column('photo_storage_key', sa.String(length=255), nullable=True))
    op.add_column('employees', sa.Column('photo_original_name', sa.String(length=255), nullable=True))
    op.add_column('employees', sa.Column('certificate_storage_key', sa.String(length=255), nullable=True))
    op.add_column('employees', sa.Column('certificate_original_name', sa.String(length=255), nullable=True))

    # --- qualifications (new table) ---
    op.create_table(
        'qualifications',
        sa.Column('party_type', sa.Enum('STUDENT', 'EMPLOYEE', name='profile_party_type'), nullable=False),
        sa.Column('party_id', sa.Uuid(), nullable=False),
        sa.Column('degree_title', sa.String(length=150), nullable=False),
        sa.Column('institution_name', sa.String(length=200), nullable=False),
        sa.Column('year_completed', sa.Integer(), nullable=True),
        sa.Column('marks_or_grade', sa.String(length=50), nullable=True),
        sa.Column('display_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_qualifications')),
    )
    op.alter_column('qualifications', 'display_order', server_default=None)
    op.create_index('ix_qualifications_party', 'qualifications', ['party_type', 'party_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_qualifications_party', table_name='qualifications')
    op.drop_table('qualifications')
    op.execute("DROP TYPE IF EXISTS profile_party_type")

    op.drop_column('employees', 'certificate_original_name')
    op.drop_column('employees', 'certificate_storage_key')
    op.drop_column('employees', 'photo_original_name')
    op.drop_column('employees', 'photo_storage_key')
    op.drop_column('employees', 'address_line')
    op.drop_column('employees', 'city')
    op.drop_column('employees', 'district')
    op.drop_column('employees', 'province')
    op.drop_column('employees', 'religion')

    op.drop_column('students', 'certificate_original_name')
    op.drop_column('students', 'certificate_storage_key')
    op.drop_column('students', 'photo_original_name')
    op.drop_column('students', 'photo_storage_key')