"""Batch terms: a term can belong to one program + semester, and a fee charged to many batches shows each student's own term.

Batches start and end at different times, so the institute registers one term per batch ("Fall 2026 BSN 8TH"). A fee
that covers several batches used to carry one of those labels, so an 8th-semester student saw "BSN 6TH" on their fee.

Production impact:
- academic_terms gets two NULL columns, program_id (-> programs, SET NULL on delete) and semester (integer). The table
  has a few dozen rows; adding NULL columns does not rewrite it. Where a term's name clearly names one program code and
  one semester ("Fall 2026 BSN 8TH" -> BSN, semester 8) both are filled in; every other term is left empty, to be filled
  in on Courses -> Academic terms. Nothing else about any term changes.
- fee_assignments gets one NULL column, term_label (text, 50). It is filled in only for fees set to "each student's own
  term", from then on. Every existing charge keeps NULL, which means "show the fee's own term" -- exactly what is shown
  today. No amounts, instalments, payments or fines are touched.
Downgrade drops the three columns (program/semester typed on terms and per-student fee terms would be lost; fees set to
each student's own term would then show the words "All batches").

Revision ID: d7f9b1c3e5a7
Revises: c6e8a0c2d4f6
"""
import re

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7f9b1c3e5a7'
down_revision = 'c6e8a0c2d4f6'
branch_labels = None
depends_on = None


def _guess(label: str, codes: dict[str, str]) -> tuple[str | None, int | None]:
    words = re.findall(r"[A-Za-z_]+|\d+(?:st|nd|rd|th)?", label, flags=re.I)
    progs = {codes[w.upper()] for w in words if w.upper() in codes}
    sems = set()
    for i, w in enumerate(words):
        m = re.fullmatch(r"(\d+)(st|nd|rd|th)", w, flags=re.I)
        if m:
            sems.add(int(m.group(1)))
        elif w.isdigit() and i > 0 and words[i - 1].lower() in ("sem", "semester"):
            sems.add(int(w))
    if len(progs) == 1 and len(sems) == 1 and 1 <= next(iter(sems)) <= 20:
        return next(iter(progs)), next(iter(sems))
    return None, None


def upgrade() -> None:
    op.add_column('academic_terms', sa.Column('program_id', sa.Uuid(), nullable=True))
    op.add_column('academic_terms', sa.Column('semester', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_academic_terms_program', 'academic_terms', 'programs', ['program_id'], ['id'], ondelete='SET NULL')
    op.add_column('fee_assignments', sa.Column('term_label', sa.String(length=50), nullable=True))

    bind = op.get_bind()
    codes = {str(code).upper(): pid for pid, code in bind.execute(sa.text("SELECT id, code FROM programs")).all() if code}
    for tid, label in bind.execute(sa.text("SELECT id, label FROM academic_terms")).all():
        program_id, semester = _guess(label or "", codes)
        if program_id is not None:
            bind.execute(sa.text("UPDATE academic_terms SET program_id = :p, semester = :s WHERE id = :id"),
                         {"p": program_id, "s": semester, "id": tid})


def downgrade() -> None:
    op.drop_column('fee_assignments', 'term_label')
    op.drop_constraint('fk_academic_terms_program', 'academic_terms', type_='foreignkey')
    op.drop_column('academic_terms', 'semester')
    op.drop_column('academic_terms', 'program_id')
