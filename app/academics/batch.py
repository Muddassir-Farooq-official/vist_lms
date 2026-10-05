"""Fetch related rows for a whole list in ONE query each, instead of one query per row.

Building a list of 150 course offerings used to run ~450 tiny queries (course, teacher and
enrolled-count for every row). Loading them together keeps a page snappy as the school grows.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.academics.models import Enrollment, EnrollmentStatus


async def load_by_ids(session: AsyncSession, model, ids: Iterable[uuid.UUID | None]) -> dict:
    """{id: row} for every id given (missing ids are simply absent)."""
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = (await session.execute(select(model).where(model.id.in_(wanted)))).scalars().all()
    return {row.id: row for row in rows}


async def enrolled_counts(session: AsyncSession, offering_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, int]:
    """{offering_id: number of currently ENROLLED students}."""
    wanted = set(offering_ids)
    if not wanted:
        return {}
    stmt = (
        select(Enrollment.course_offering_id, func.count())
        .where(Enrollment.course_offering_id.in_(wanted), Enrollment.status == EnrollmentStatus.ENROLLED)
        .group_by(Enrollment.course_offering_id)
    )
    return {oid: n for oid, n in (await session.execute(stmt)).all()}