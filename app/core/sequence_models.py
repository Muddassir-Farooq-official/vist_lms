from __future__ import annotations

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UUIDPKMixin


class SequenceCounter(UUIDPKMixin, Base):
    """Backs every system-generated human-readable identifier in this
    app (student roll numbers, employee IDs) with a single, generic,
    race-safe counter table — one row per named scope
    (e.g. 'student_roll:BSN:2026', 'employee_id'), rather than a
    separate bespoke counter mechanism per entity type.

    Deliberately NOT 'count existing rows + 1' — that pattern is
    documented elsewhere in this codebase (see BookService's accession
    code comment) as race-prone under concurrent creation, which
    matters here even more since a CSV import job can create many
    students in the same program/year in quick succession. See
    app/core/sequences.py for the atomic allocate-and-increment logic
    that uses this table."""

    __tablename__ = "sequence_counters"
    __table_args__ = (UniqueConstraint("scope_key", name="uq_sequence_counters_scope_key"),)

    scope_key: Mapped[str] = mapped_column(String(150), nullable=False)
    # The NEXT value to be handed out. Starts at 2 on first insert because
    # the insert itself represents claiming value 1 — see next_sequence_value().
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)