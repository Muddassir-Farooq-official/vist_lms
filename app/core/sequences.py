from __future__ import annotations

import uuid

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sequence_models import SequenceCounter


async def next_sequence_value(session: AsyncSession, scope_key: str) -> int:
    """Atomically allocates the next integer for a named scope via a
    single UPSERT (INSERT ... ON CONFLICT DO UPDATE ... RETURNING) rather
    than a read-then-write — race-safe under concurrent creation without
    needing an explicit row lock, since the UPSERT itself serializes on
    the unique index. First call for a new scope_key returns 1."""
    stmt = (
        pg_insert(SequenceCounter)
        .values(id=uuid.uuid4(), scope_key=scope_key, next_value=2)
        .on_conflict_do_update(
            index_elements=["scope_key"],
            set_={"next_value": SequenceCounter.next_value + 1},
        )
        .returning(SequenceCounter.next_value)
    )
    result = await session.execute(stmt)
    claimed_next_value = result.scalar_one()
    return claimed_next_value - 1


async def generate_roll_number(session: AsyncSession, *, year: int) -> str:
    """<2-digit enrollment year><sequence>, e.g. '24001' for the 1st
    admission of 2024, '24194' for the 194th — approved format, global
    across all programs (not per-program — a single institution-wide
    admission sequence per year, matching how a real admission/roll
    register works). Zero-padded to at least 3 digits but grows past
    that naturally if a single year admits 1000+ students. The roll
    number is never regenerated once issued, since it's also the
    permanent login username.

    NOTE: this replaced an earlier '<PROGRAM_CODE>-<YEAR>-<SEQ>' format
    (e.g. 'BSN-2026-0001') per a later approved decision.

    UPDATE — this docstring is now only half-right, kept for the
    history: migration e2b7c4f91a6d ("retroactively rename existing
    student roll numbers to the new format") later REVERSED the
    "don't retroactively rename" policy this comment used to describe,
    per a subsequent explicit approved decision — every existing
    student's roll_number and username were rewritten to this format.
    See that migration for the exact backfill logic and its operational
    warning (every existing student's login username changed as a
    result). Nothing about the generator function below changed; only
    the policy on old data did."""
    short_year = year % 100
    scope_key = f"student_roll:{year}"
    seq = await next_sequence_value(session, scope_key)
    return f"{short_year:02d}{seq:03d}"


async def generate_employee_id(session: AsyncSession) -> str:
    """EMP-0001, EMP-0002, ... — a single global sequence, not scoped
    per job role/department (ASSUMPTION — approved decision was
    'auto-generated, sequential', this is the interpretation
    implemented)."""
    seq = await next_sequence_value(session, "employee_id")
    return f"EMP-{seq:04d}"