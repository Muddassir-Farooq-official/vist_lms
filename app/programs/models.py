from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPKMixin


class Program(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "programs"
    __table_args__ = (UniqueConstraint("code", name="uq_programs_code"),)

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    duration_years: Mapped[int] = mapped_column(Integer, nullable=False)
    total_semesters: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Per-program cap on how many credit hours a student can carry in one
    # semester (approved decision: configurable per program, not a single
    # global constant) — this is the actual enrollment/retake gate; see
    # app/academics/service.py EnrollmentService.
    max_credit_hours_per_semester: Mapped[int] = mapped_column(Integer, nullable=False, default=18)


# Case-insensitive uniqueness on trimmed name (Section 9/11) — declared as a
# standalone Index referencing the mapped column, since a functional index
# expression can't reference `Program.name` from inside the class body
# (the column doesn't exist yet at that point). Created explicitly via raw
# SQL in migration 221008dd59b1; declared here purely so `alembic check`
# has an ORM-side object to compare against and doesn't flag it as drift.
Index("uq_programs_name_lower", func.lower(func.trim(Program.name)), unique=True)

# NOTE: `alembic check` will report a permanent cosmetic diff here —
# Postgres canonicalizes `trim(name)` to `TRIM(BOTH FROM name)` in its
# system catalogs (pg_get_indexdef), so the ORM-declared expression never
# textually matches what pg_catalog reports back, even though they are the
# same index. This is a known, harmless false-positive; do not "fix" it by
# switching to raw SQL for this index.